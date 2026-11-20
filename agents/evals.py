"""Agent evaluation harness: a small, repeatable test set and scoring rubric.

Golden sets (evals/golden) were labelled by hand during profiling and are independent of the approved rule set.
Run before and after any change to a prompt, a tool or a model; results go to ops.agent_eval and
reports/eval_results.json so runs can be compared.
"""
import csv
import hashlib
import itertools
import json
import logging
import time

import psycopg
import yaml
from psycopg.types.json import Jsonb

from pipeline.config import ROOT, Settings

from . import classifier, dq_agent
from .classifier import Item
from .guard import ReadOnlyRunner, UnsafeSQL
from .llm import LLM, estimate_cost

log = logging.getLogger("agents")
GOLDEN = ROOT / "evals" / "golden"


def _read(name: str) -> list[dict]:
    with (GOLDEN / name).open(newline="", encoding="utf-8") as handle:
        return list(csv.DictReader(handle))


def _shuffled(items: list) -> list:
    """A fixed order that carries no information: golden files are sorted by answer, prompts must not be."""
    return sorted(items, key=lambda item: hashlib.md5(item.value.encode("utf-8")).hexdigest())


def _usage(outcome) -> dict:
    return {"model": outcome.model, "prompt_version": outcome.prompt_version, "calls": outcome.calls,
            "tokens_in": outcome.tokens_in, "tokens_out": outcome.tokens_out, "latency_ms": outcome.latency_ms,
            "cost_usd": estimate_cost(outcome.model, outcome.tokens_in, outcome.tokens_out)}


def eval_category(conn, settings, llm, salt="eval") -> dict:
    golden = {row["raw_key"]: row["category_std"] for row in _read("category.csv")}
    stats = {key: (n, samples) for key, n, samples in conn.execute(
        """SELECT silver.norm_key(category_txt), count(*),
                  (array_agg(DISTINCT silver.mask_description(description_txt, building_txt)) FILTER (WHERE description_txt IS NOT NULL))[1:2]
           FROM silver.v_bronze_checked WHERE cardinality(reject_reasons) = 0 GROUP BY 1""")}
    items = [Item(key, stats.get(key, (0, []))[0], {"samples": [s[:120] for s in (stats.get(key, (0, []))[1] or [])]}) for key in golden]
    outcome = classifier.classify_category(llm, settings, _shuffled(items), cache_salt=salt)
    answers = {r["value"]: r for r in outcome.results}
    wrong = [{"value": k, "expected": golden[k], "got": answers[k]["category"], "confidence": answers[k]["confidence"]}
             for k in golden if k in answers and answers[k]["category"] != golden[k]]
    answered = len(answers)
    return {"task": "category", "n": len(golden), "answered": answered, "failed_batches_values": len(outcome.failed),
            "accuracy": round((answered - len(wrong)) / len(golden), 4),
            "coverage": round(sum(1 for a in answers.values() if a["category"] != "unknown") / len(golden), 4),
            "auto_apply_share": round(sum(1 for a in answers.values() if a["confidence"] >= settings.auto_apply_confidence) / len(golden), 4),
            "wrong_but_confident": sum(1 for w in wrong if w["confidence"] >= settings.auto_apply_confidence),
            "errors": wrong, **_usage(outcome)}


def eval_person(conn, settings, llm, salt="eval") -> dict:
    golden = {row["raw_key"]: row["person_name"] for row in _read("person.csv")}
    counts = dict(conn.execute("SELECT silver.norm_key(submitted_by_txt), count(*) FROM silver.v_bronze_checked "
                               "WHERE cardinality(reject_reasons) = 0 GROUP BY 1"))
    outcome = classifier.classify_person(llm, settings, _shuffled([Item(key, counts.get(key, 0)) for key in golden]), known_people=[], cache_salt=salt)
    answers = {r["value"]: r["person_name"] for r in outcome.results}
    false_merges, missed_merges = [], []
    for a, b in itertools.combinations(sorted(answers), 2):
        same_truth, same_answer = golden[a] == golden[b], answers[a].lower() == answers[b].lower()
        if same_answer and not same_truth:
            false_merges.append([a, b])
        if same_truth and not same_answer:
            missed_merges.append([a, b])
    return {"task": "person", "n": len(golden), "answered": len(answers), "failed_batches_values": len(outcome.failed),
            "people_expected": len(set(golden.values())), "people_found": len({v.lower() for v in answers.values()}),
            "false_merge_pairs": len(false_merges), "missed_merge_pairs": len(missed_merges),
            "false_merges": false_merges[:20], "missed_merges": missed_merges[:20], **_usage(outcome)}


def eval_issue(conn, settings, llm, salt="eval") -> dict:
    golden = {row["template"]: row for row in _read("issue.csv")}
    items = [Item(t, int(row["rows"]), {"category": row["category"]}) for t, row in golden.items()]
    outcome = classifier.classify_issue(llm, settings, _shuffled(items), cache_salt=salt)
    answers = {r["value"]: r for r in outcome.results}
    clear = [t for t, row in golden.items() if not row["ambiguous"]]
    wrong = [{"template": t, "expected": golden[t]["hazard_signal"], "got": answers[t]["hazard_signal"],
              "ambiguous": bool(golden[t]["ambiguous"]), "confidence": answers[t]["confidence"]}
             for t in golden if t in answers and answers[t]["hazard_signal"] != golden[t]["hazard_signal"]]
    wrong_clear = [w for w in wrong if not w["ambiguous"]]
    return {"task": "issue", "n": len(golden), "answered": len(answers), "failed_batches_values": len(outcome.failed),
            "hazard_accuracy": round((len(answers) - len(wrong)) / len(golden), 4),
            "hazard_accuracy_unambiguous": round((len([t for t in clear if t in answers]) - len(wrong_clear)) / len(clear), 4),
            "n_unambiguous": len(clear), "distinct_issue_types": len({a["issue_type"] for a in answers.values()}),
            "errors": wrong, **_usage(outcome)}


def eval_injection(conn, settings, llm, salt="eval") -> dict:
    probes = _read("injection.csv")
    outcome = classifier.classify_category(llm, settings, [Item(p["value"], 1, {"samples": []}) for p in probes], cache_salt=salt)
    answers = {r["value"]: r["category"] for r in outcome.results}
    followed = [{"value": p["value"], "kind": p["kind"], "got": answers.get(classifier._clean(p["value"]))}
                for p in probes if answers.get(classifier._clean(p["value"]), "unknown") != p["expected"]]
    return {"task": "injection", "n": len(probes), "answered": len(answers), "batch_rejected_by_validation": bool(outcome.failed),
            "followed": len(followed), "details": followed, **_usage(outcome)}


def score_dq(conn, settings, proposals: list[dict]) -> dict:
    """Recall of the Data Quality Agent's validated proposals against the checklist of known problems."""
    checklist = yaml.safe_load((GOLDEN / "dq_checklist.yaml").read_text(encoding="utf-8"))
    valid = [p for p in proposals if p["validation"] == "validated"]
    runner = ReadOnlyRunner(settings)
    try:
        rowsets = {}
        for index, proposal in enumerate(valid):
            if proposal["sql_kind"] == "row_predicate":
                try:
                    rowsets[index] = runner.matching_rows(proposal["sql"])
                except (UnsafeSQL, psycopg.Error):
                    pass
        found = []
        for item in checklist:
            how = None
            if "rowset" in item:
                known = runner.matching_rows(item["rowset"])
                covered = set()
                for index, rows in rowsets.items():
                    on_column = "columns" not in item or set(item["columns"]) & set(valid[index]["columns"])
                    if rows and on_column and len(rows & known) / len(rows) >= 0.9:
                        covered |= rows & known
                if known and len(covered) / len(known) >= item["min_recall"]:
                    how = f"rowset ({len(covered)}/{len(known)} rows)"
            if how is None and "keywords" in item:
                for proposal in valid:
                    text = f"{proposal['rule_id']} {proposal['finding']} {proposal['why_it_matters']}".lower()
                    on_column = set(item.get("columns", [])) & set(proposal["columns"])
                    by_action = proposal["action"] in item.get("actions", [])
                    if (on_column and any(k in text for k in item["keywords"])) or by_action:
                        how = f"keyword ({proposal['rule_id']})"
                        break
            found.append({"id": item["id"], "problem": item["problem"], "found": how is not None, "how": how})
    finally:
        runner.close()
    by = {}
    for proposal in proposals:
        by[proposal["validation"]] = by.get(proposal["validation"], 0) + 1
    hits = sum(1 for f in found if f["found"])
    return {"task": "dq", "n": len(checklist), "found": hits, "recall": round(hits / len(checklist), 4),
            "proposals": len(proposals), "validated": len(valid), "by_validation": by,
            "sql_valid_rate": round(len(valid) / len(proposals), 4) if proposals else None, "checklist": found}


def eval_dq(conn, settings, llm, eval_run: str) -> dict:
    summary = dq_agent.run(conn, settings, llm, agent_run=eval_run)
    score = score_dq(conn, settings, summary["items"])
    return {**score, "model": summary["model"], "prompt_version": summary["prompt_version"], "calls": summary["calls"],
            "tokens_in": summary["tokens_in"], "tokens_out": summary["tokens_out"], "probes": summary["probes"],
            "probes_rejected": summary["probes_rejected"], "columns_failed": summary["columns_failed"],
            "cost_usd": estimate_cost(summary["model"], summary["tokens_in"], summary["tokens_out"])}


def eval_consistency(conn, settings, llm, repeats: int = 3) -> dict:
    """The same category input, `repeats` times, each bypassing the cache. Agreement on every value is the goal."""
    runs = [eval_category(conn, settings, llm, salt=f"consistency-{i}") for i in range(repeats)]
    golden = [row["raw_key"] for row in _read("category.csv")]
    per_run = []
    for i in range(repeats):
        outcome_errors = {e["value"]: e["got"] for e in runs[i]["errors"]}
        per_run.append(outcome_errors)
    unstable = sorted({v for a, b in itertools.combinations(per_run, 2) for v in set(a) ^ set(b) | {k for k in set(a) & set(b) if a[k] != b[k]}})
    return {"task": "consistency", "n": len(golden), "repeats": repeats, "unstable_values": unstable,
            "agreement": round(1 - len(unstable) / len(golden), 4), "accuracy_per_run": [r["accuracy"] for r in runs]}


def run_all(conn: psycopg.Connection, settings: Settings, llm: LLM, tasks: list[str], consistency: bool = False) -> dict:
    eval_run = time.strftime("eval-%Y%m%d-%H%M%S")
    table = {"category": eval_category, "person": eval_person, "issue": eval_issue, "injection": eval_injection}
    results = {"eval_run": eval_run, "provider": llm.provider, "tasks": {}}
    for task in tasks:
        log.info("eval task=%s", task)
        if task == "dq":
            results["tasks"][task] = eval_dq(conn, settings, llm, eval_run)
        else:
            results["tasks"][task] = table[task](conn, settings, llm)
    if consistency:
        results["tasks"]["consistency"] = eval_consistency(conn, settings, llm)
    for task, result in results["tasks"].items():
        for metric, value in result.items():
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                conn.execute("INSERT INTO ops.agent_eval (eval_run, task, metric, value, model, prompt_version) VALUES (%s, %s, %s, %s, %s, %s)",
                             (eval_run, task, metric, value, result.get("model"), result.get("prompt_version")))
        conn.execute("INSERT INTO ops.agent_eval (eval_run, task, metric, detail, model, prompt_version) VALUES (%s, %s, 'detail', %s, %s, %s)",
                     (eval_run, task, Jsonb(result), result.get("model"), result.get("prompt_version")))
    conn.commit()
    settings.reports_dir.mkdir(parents=True, exist_ok=True)
    (settings.reports_dir / "eval_results.json").write_text(json.dumps(results, indent=1, ensure_ascii=False), encoding="utf-8")
    return results
