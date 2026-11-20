"""Data Quality Agent.

Reads the statistical profile of bronze (never the rows), may run a few read-only probe queries, and
proposes cleaning and validation rules: the finding with numbers, why it matters, and SQL that identifies the
affected rows. Every proposal is then checked by code, not trusted:
  * the SQL passes the text guard and runs read-only as a role that can only SELECT from bronze
  * the number of rows it matches is measured and compared with the number the agent claimed
Proposals are stored in ops.rule_proposal. Nothing changes in silver until a person approves; approved
row predicates then run as checks on every load.

The investigation is one bounded loop per column plus one for cross-column findings. The plan is fixed and
the context stays small; the freedom the model has is which probes to run and what to propose.
"""
import json
import logging
import time

import psycopg

from pipeline import profiler
from pipeline.config import Settings

from .guard import ReadOnlyRunner, UnsafeSQL
from .llm import LLM, BudgetExceeded, LLMOutputError, load_prompt

log = logging.getLogger("agents")
MAX_PROBE_ROUNDS = 2
MAX_PROBES_PER_ROUND = 3
ACTIONS = ["standardise", "set_null", "flag", "quarantine", "deduplicate"]

SCHEMA = {
    "type": "object", "additionalProperties": False, "required": ["probes", "proposals"],
    "properties": {
        "probes": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["purpose", "sql"],
            "properties": {"purpose": {"type": "string"}, "sql": {"type": "string"}}}},
        "proposals": {"type": "array", "items": {
            "type": "object", "additionalProperties": False,
            "required": ["rule_id", "columns", "finding", "why_it_matters", "action", "sql_kind", "sql", "risk", "expected_rows"],
            "properties": {
                "rule_id": {"type": "string"},
                "columns": {"type": "array", "items": {"type": "string"}},
                "finding": {"type": "string"},
                "why_it_matters": {"type": "string"},
                "action": {"type": "string", "enum": ACTIONS},
                "sql_kind": {"type": "string", "enum": ["row_predicate", "count_query"]},
                "sql": {"type": "string"},
                "risk": {"type": "string", "enum": ["low", "high"]},
                "expected_rows": {"type": "integer"}}}},
    },
}


def _overview(profile: dict) -> str:
    columns = ", ".join(f"{c['column']} ({c['tags']['semantic_type']})" for c in profile["columns"])
    return (f"TABLE: {profile['table']}, {profile['rows']} rows. {profile['purpose']}\n"
            f"COLUMNS (all stored as text): {columns}")


def investigate(llm: LLM, runner: ReadOnlyRunner, focus_title: str, focus: dict, overview: str, stats: dict) -> list[dict]:
    """One bounded tool-use loop: profile in, optional probe rounds, proposals out."""
    version, system = load_prompt("dq")
    user = f"{overview}\n\nFOCUS: {focus_title}\n{json.dumps(focus, ensure_ascii=False)}"
    for round_number in range(MAX_PROBE_ROUNDS + 1):
        result = llm.complete_json(task="dq_propose", prompt_version=version, system=system, user=user, schema=SCHEMA,
                                   tier="strong")
        stats["model"], stats["prompt_version"] = result.model, version
        stats["calls"] += 0 if result.cached else 1
        stats["tokens_in"] += result.input_tokens
        stats["tokens_out"] += result.output_tokens
        probes = result.data["probes"][:MAX_PROBES_PER_ROUND]
        if not probes or round_number == MAX_PROBE_ROUNDS:
            return result.data["proposals"]
        answers = []
        for probe in probes:
            stats["probes"] += 1
            try:
                answers.append({"purpose": probe["purpose"], "sql": probe["sql"], "rows": runner.probe(probe["sql"])})
            except (UnsafeSQL, psycopg.Error) as exc:
                stats["probes_rejected"] += 1
                answers.append({"purpose": probe["purpose"], "sql": probe["sql"], "error": str(exc).splitlines()[0][:200]})
        rounds_left = MAX_PROBE_ROUNDS - round_number - 1
        user += (f"\n\nPROBE RESULTS (round {round_number + 1}):\n{json.dumps(answers, ensure_ascii=False)}\n"
                 f"Return your proposals now. Ask for further probes only if essential ({rounds_left} round(s) left).")
    return []


def validate(runner: ReadOnlyRunner, proposal: dict, tolerance: float) -> dict:
    """Check a proposal's SQL and its claimed row count against the database."""
    try:
        measured = runner.count(proposal["sql"], proposal["sql_kind"])
    except UnsafeSQL as exc:
        return {"validation": "rejected_unsafe_sql", "measured_rows": None, "validation_msg": str(exc)}
    except psycopg.Error as exc:
        return {"validation": "rejected_sql_error", "measured_rows": None, "validation_msg": str(exc).splitlines()[0][:300]}
    expected = proposal["expected_rows"]
    allowed_gap = max(1, tolerance * max(measured, expected))
    if abs(measured - expected) > allowed_gap:
        return {"validation": "rejected_count_mismatch", "measured_rows": measured,
                "validation_msg": f"claimed {expected} rows, measured {measured}"}
    return {"validation": "validated", "measured_rows": measured, "validation_msg": ""}


def run(conn: psycopg.Connection, settings: Settings, llm: LLM, agent_run: str | None = None) -> dict:
    """Profile bronze, investigate every column and the cross-column findings, validate and store proposals."""
    agent_run = agent_run or time.strftime("dq-%Y%m%d-%H%M%S")
    profile = profiler.build(conn)
    profiler.write(profile, settings.reports_dir)
    overview = _overview(profile)
    stats = {"agent_run": agent_run, "model": "", "prompt_version": "", "calls": 0, "tokens_in": 0, "tokens_out": 0,
             "probes": 0, "probes_rejected": 0, "columns_failed": [], "stopped_on_budget": False}
    focuses = [(f"column {c['column']}", c) for c in profile["columns"]]
    focuses.append(("relationships between columns (near-duplicate rows, values in the wrong column, timestamp order)",
                    profile["cross_column"]))
    runner = ReadOnlyRunner(settings)
    stored = []
    try:
        for title, focus in focuses:
            try:
                proposals = investigate(llm, runner, title, focus, overview, stats)
            except BudgetExceeded as exc:
                log.warning("dq agent stopped: %s", exc)
                stats["stopped_on_budget"] = True
                break
            except LLMOutputError as exc:
                log.warning("dq agent: %s failed: %s", title, exc)
                stats["columns_failed"].append(title)
                continue
            for proposal in proposals:
                check = validate(runner, proposal, settings.count_tolerance)
                conn.execute(
                    "INSERT INTO ops.rule_proposal (agent_run, rule_id, column_names, finding, why_it_matters, action, sql_kind, sql, "
                    "risk, expected_rows, measured_rows, validation, validation_msg, status, model, prompt_version) "
                    "VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, 'proposed', %s, %s)",
                    (agent_run, proposal["rule_id"][:80], proposal["columns"], proposal["finding"], proposal["why_it_matters"],
                     proposal["action"], proposal["sql_kind"], proposal["sql"], proposal["risk"], proposal["expected_rows"],
                     check["measured_rows"], check["validation"], check["validation_msg"], stats["model"], stats["prompt_version"]))
                stored.append({**proposal, **check})
            conn.commit()
    finally:
        runner.close()
    outcomes = {}
    for proposal in stored:
        outcomes[proposal["validation"]] = outcomes.get(proposal["validation"], 0) + 1
    return {**stats, "proposals": len(stored), "by_validation": outcomes, "items": stored}
