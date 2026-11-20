"""Builds reports/agent_report.md from what was actually recorded: evaluations, proposals, calls.

Nothing here is written by hand. If no live model call has been recorded, the report says so.
"""
import json
import time
from pathlib import Path

import psycopg

from pipeline.config import ROOT, Settings

from .llm import estimate_cost

START, END = "<!-- AGENT_RESULTS:START -->", "<!-- AGENT_RESULTS:END -->"


def _table(headers: list[str], rows: list[list]) -> str:
    lines = ["| " + " | ".join(headers) + " |", "|" + "|".join("---" for _ in headers) + "|"]
    lines += ["| " + " | ".join(str(c).replace("|", "\\|").replace("\n", " ") for c in row) + " |" for row in rows]
    return "\n".join(lines)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[:limit] + f"\n... [{len(text) - limit} more characters]"


def build(conn: psycopg.Connection) -> tuple[str, str]:
    """Returns (full report, short summary for the README)."""
    calls = conn.execute(
        "SELECT task, model, count(*), sum(input_tokens), sum(output_tokens), round(avg(latency_ms)) FROM ops.agent_call "
        "WHERE provider NOT IN ('stub', 'replay') GROUP BY 1, 2 ORDER BY 1, 2").fetchall()
    if not calls:
        message = ("No live agent run has been recorded in this database yet. Configure a provider in `.env`, then run "
                   "`python -m pipeline evals` and `python -m pipeline report`.")
        return f"# Agent run report\n\n{message}\n", message

    out = ["# Agent run report", "", f"Generated {time.strftime('%Y-%m-%d %H:%M:%S %Z')} from ops.agent_eval, ops.rule_proposal and ops.agent_call.",
           "Every number below was measured in a live run; nothing is written by hand.", ""]
    summary = []

    out += ["## Model usage", "", _table(
        ["Task", "Model", "Calls", "Tokens in", "Tokens out", "Mean latency (ms)", "Cost estimate (USD)"],
        [[t, m, n, i, o, lat, (lambda c: "n/a" if c is None else f"{c:.4f}")(estimate_cost(m, i, o))] for t, m, n, i, o, lat in calls]), ""]

    latest = conn.execute("SELECT eval_run FROM ops.agent_eval ORDER BY created_at DESC LIMIT 1").fetchone()
    if latest:
        details = {task: detail for task, detail in conn.execute(
            "SELECT task, detail FROM ops.agent_eval WHERE eval_run = %s AND metric = 'detail'", (latest[0],))}
        out += [f"## Evaluation against the golden sets (run {latest[0]})", ""]
        rows = []
        if "category" in details:
            d = details["category"]
            rows.append(["Category map", d["model"], f"{d['accuracy']:.1%} exact match on {d['n']} category keys",
                         f"coverage {d['coverage']:.1%}; {d['wrong_but_confident']} wrong at or above the auto-apply threshold"])
        if "person" in details:
            d = details["person"]
            rows.append(["Submitter resolution", d["model"], f"{d['people_found']} people found, {d['people_expected']} expected",
                         f"{d['false_merge_pairs']} false-merge pairs, {d['missed_merge_pairs']} missed-merge pairs"])
        if "issue" in details:
            d = details["issue"]
            rows.append(["Hazard signal", d["model"], f"{d['hazard_accuracy_unambiguous']:.1%} on {d['n_unambiguous']} unambiguous templates",
                         f"{d['hazard_accuracy']:.1%} on all {d['n']}; {d['distinct_issue_types']} distinct issue types"])
        if "injection" in details:
            d = details["injection"]
            rows.append(["Injection probes", d["model"], f"{d['followed']} of {d['n']} followed",
                         "batch rejected by validation" if d["batch_rejected_by_validation"] else "all answers passed validation"])
        if "dq" in details:
            d = details["dq"]
            rows.append(["Data Quality Agent", d["model"], f"{d['found']} of {d['n']} known problems found",
                         f"{d['validated']} of {d['proposals']} proposals passed the SQL and row-count checks; {d['probes']} probes"])
        if "consistency" in details:
            d = details["consistency"]
            rows.append(["Consistency", "", f"{d['agreement']:.1%} agreement over {d['repeats']} uncached runs", f"unstable values: {len(d['unstable_values'])}"])
        out += [_table(["Task", "Model", "Result", "Detail"], rows), ""]
        summary += [_table(["Task", "Model", "Result", "Detail"], rows), ""]
        for task, title, columns in [("category", "Category errors", ["value", "expected", "got", "confidence"]),
                                     ("issue", "Hazard disagreements", ["template", "expected", "got", "ambiguous", "confidence"])]:
            if details.get(task, {}).get("errors"):
                out += [f"### {title}", "", _table(columns, [[e[c] for c in columns] for e in details[task]["errors"]]), ""]
        if details.get("person", {}).get("false_merges") or details.get("person", {}).get("missed_merges"):
            d = details["person"]
            out += ["### Submitter resolution errors", "", f"False merges: {d['false_merges']}", "", f"Missed merges: {d['missed_merges']}", ""]
        if "dq" in details:
            out += ["### Data quality checklist", "", _table(["Known problem", "Found", "How"], [[c["problem"], "yes" if c["found"] else "no", c["how"] or ""] for c in details["dq"]["checklist"]]), ""]

    run = conn.execute("SELECT agent_run FROM ops.rule_proposal ORDER BY id DESC LIMIT 1").fetchone()
    if run:
        proposals = conn.execute(
            "SELECT rule_id, array_to_string(column_names, ', '), action, risk, validation, expected_rows, measured_rows, finding, why_it_matters, sql, status "
            "FROM ops.rule_proposal WHERE agent_run = %s ORDER BY id", (run[0],)).fetchall()
        out += [f"## Data Quality Agent proposals (run {run[0]})", "", _table(
            ["Rule", "Columns", "Action", "Risk", "Check", "Claimed rows", "Measured rows", "Finding", "Why it matters", "SQL", "Status"],
            [[r[0], r[1], r[2], r[3], r[4], r[5], r[6], r[7], r[8], f"`{_clip(r[9], 160)}`", r[10]] for r in proposals]), ""]

    out += ["## Sample input and output (verbatim, one call per task)", ""]
    for task, in conn.execute("SELECT DISTINCT task FROM ops.agent_call WHERE provider NOT IN ('stub', 'replay') ORDER BY 1"):
        model, version, request, response = conn.execute(
            "SELECT model, prompt_version, request, response FROM ops.agent_call WHERE task = %s AND provider NOT IN ('stub', 'replay') "
            "ORDER BY created_at LIMIT 1", (task,)).fetchone()
        out += [f"### {task} ({model}, prompt {version})", "", "Input (user message; the system prompt is in agents/prompts/):", "", "```",
                _clip(request["user"], 1800), "```", "", "Output:", "", "```json", _clip(json.dumps(response, indent=1, ensure_ascii=False), 1800), "```", ""]
    return "\n".join(out), "\n".join(summary) if summary else "A live agent run is recorded; see reports/agent_report.md."


def write(conn: psycopg.Connection, settings: Settings, update_readme: bool = False) -> Path:
    report, summary = build(conn)
    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    path = settings.reports_dir / "agent_report.md"
    path.write_text(report, encoding="utf-8")
    readme = ROOT / "README.md"
    if update_readme and readme.exists():
        text = readme.read_text(encoding="utf-8")
        if START in text and END in text:
            head, rest = text.split(START, 1)
            _, tail = rest.split(END, 1)
            readme.write_text(f"{head}{START}\n{summary}\n{END}{tail}", encoding="utf-8")
    return path
