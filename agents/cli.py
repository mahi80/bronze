"""Agent commands, registered on the pipeline command line."""
import json
import logging
from pathlib import Path

from pipeline import db, profiler

from . import classifier, dq_agent, evals, report, review
from .llm import LLM, LLMDisabled

log = logging.getLogger("agents")
TASKS = ["category", "person", "issue"]


def _connect(settings):
    conn = db.connect(settings)
    db.migrate(conn)
    return conn


def cmd_profile(args, settings) -> int:
    """Write the statistical profile of bronze that the Data Quality Agent reads."""
    conn = _connect(settings)
    profile = profiler.build(conn)
    print(json.dumps({"written": str(profiler.write(profile, settings.reports_dir)), "cross_column": profile["cross_column"]}, indent=1))
    return 0


def run_agent(conn, settings, llm: LLM, agent: str, task: str = "all") -> dict:
    """Run one agent and store its proposals. Raises LLMDisabled when no provider is configured."""
    summary = {}
    if agent == "dq":
        result = dq_agent.run(conn, settings, llm)
        summary["dq"] = {k: v for k, v in result.items() if k != "items"}
        return summary
    for name in (TASKS if task == "all" else [task]):
        if name == "category":
            items, held = classifier.category_candidates(conn, settings.classify_min_rows)
            outcome = classifier.classify_category(llm, settings, items) if items else classifier.Outcome("classify_category")
        elif name == "person":
            items, held, known = classifier.person_candidates(conn, settings.classify_min_rows)
            outcome = classifier.classify_person(llm, settings, items, known) if items else classifier.Outcome("classify_person")
        else:
            items, held = classifier.issue_candidates(conn, settings.classify_min_rows)
            outcome = classifier.classify_issue(llm, settings, items) if items else classifier.Outcome("classify_issue")
        stored = classifier.store(conn, settings, outcome)
        summary[name] = {"sent": len(items), "held_below_min_rows": len(held), "failed_validation": len(outcome.failed),
                         "held_on_budget": len(outcome.held), **stored, "calls": outcome.calls,
                         "tokens_in": outcome.tokens_in, "tokens_out": outcome.tokens_out}
    return summary


def cmd_agents(args, settings) -> int:
    """Run the agents on values that have no mapping yet (classify) or on the bronze profile (dq)."""
    conn = _connect(settings)
    try:
        summary = run_agent(conn, settings, LLM(settings, conn), args.agent, args.task)
    except LLMDisabled as exc:
        log.error("agents are not configured: %s", exc)
        return 3
    print(json.dumps(summary, indent=1))
    log.info("review with: python -m pipeline proposals; then python -m pipeline approve ...; then python -m pipeline run")
    return 0


def cmd_proposals(args, settings) -> int:
    """List everything waiting for a person's decision."""
    rows = review.pending(_connect(settings))
    for row in rows:
        confidence = "" if row["confidence"] is None else f"{row['confidence']:.2f}"
        print(f"{row['task']:9} {row['status']:24} {confidence:5} {row['key'][:60]!r:62} -> {row['value']}   # {row['reason'] or ''}")
    print(f"{len(rows)} pending")
    return 0


def cmd_approve(args, settings) -> int:
    """Approve (or reject) proposals. This is the only way an agent's output becomes part of the rule set."""
    conn = _connect(settings)
    if args.rule:
        count = review.review_rules(conn, args.rule, settings.approver, "rejected" if args.reject else "approved")
    elif not args.task:
        log.error("give --task or --rule")
        return 2
    elif args.reject:
        count = review.reject_mappings(conn, args.task, settings.approver, args.key or [])
    else:
        if not (args.key or args.all or args.min_confidence is not None):
            log.error("give --key, --min-confidence or --all")
            return 2
        count = review.approve_mappings(conn, args.task, settings.approver, args.key, args.min_confidence, args.set)
    print(json.dumps({"changed": count, "by": settings.approver}))
    return 0


def cmd_evals(args, settings) -> int:
    """Score the agents against the golden sets. Needs a model provider."""
    conn = _connect(settings)
    try:
        results = evals.run_all(conn, settings, LLM(settings, conn), args.tasks, args.consistency)
    except LLMDisabled as exc:
        log.error("agents are not configured: %s", exc)
        return 3
    brief = {task: {k: v for k, v in r.items() if not isinstance(v, (list, dict))} for task, r in results["tasks"].items()}
    print(json.dumps({"eval_run": results["eval_run"], "tasks": brief}, indent=1))
    return 0


class _Preview(Exception):
    pass


def cmd_prompts(args, settings) -> int:
    """Write the exact prompts the agents send for this data to docs/prompt_preview.md, without calling a model."""
    from pipeline.config import ROOT

    conn = _connect(settings)
    captured = []

    def capture(model, system, user, schema, max_tokens):
        captured.append((system, user, schema))
        raise _Preview()

    llm = LLM(settings, None, transport=capture)
    profile = profiler.build(conn)
    jobs = [
        ("Category standardisation", lambda: evals.eval_category(conn, settings, llm)),
        ("Submitter resolution", lambda: evals.eval_person(conn, settings, llm)),
        ("Issue type and hazard signal", lambda: evals.eval_issue(conn, settings, llm)),
        ("Data Quality Agent: one column (created_at)", lambda: dq_agent.investigate(
            llm, None, "column created_at", next(c for c in profile["columns"] if c["column"] == "created_at"),
            dq_agent._overview(profile), {"calls": 0, "tokens_in": 0, "tokens_out": 0, "probes": 0, "probes_rejected": 0})),
        ("Data Quality Agent: relationships between columns", lambda: dq_agent.investigate(
            llm, None, "relationships between columns", profile["cross_column"], dq_agent._overview(profile),
            {"calls": 0, "tokens_in": 0, "tokens_out": 0, "probes": 0, "probes_rejected": 0})),
    ]
    out = ["# Prompt preview", "",
           "The exact prompts built from `data/raw_tickets.csv`, written by `python -m pipeline prompts`. No model was called.",
           "User messages are cut after 25 lines; the full input has the same shape.", ""]
    for title, job in jobs:
        try:
            job()
        except _Preview:
            pass
        system, user, schema = captured[-1]
        lines = user.splitlines()
        shown = lines[:25] + ([f"... [{len(lines) - 25} more lines]"] if len(lines) > 25 else [])
        shown = [line if len(line) <= 1500 else line[:1500] + f" ... [{len(line) - 1500} more characters]" for line in shown]
        out += [f"## {title}", "", "**System prompt**", "", "```text", system, "```", "", "**User message**", "", "```text", *shown, "```", "",
                "**Output schema (enforced by the provider)**", "", "```json", json.dumps(schema), "```", ""]
    path = ROOT / "docs" / "prompt_preview.md"
    path.write_text("\n".join(out), encoding="utf-8")
    print(path)
    return 0


def cmd_report(args, settings) -> int:
    """Write reports/agent_report.md from recorded runs; --update-readme refreshes the README summary."""
    print(report.write(_connect(settings), settings, args.update_readme))
    return 0


def cmd_export_recordings(args, settings) -> int:
    """Save live model calls as files, so evaluations can be replayed without a key (LLM_PROVIDER=replay)."""
    conn = _connect(settings)
    target = Path(settings.replay_dir)
    target.mkdir(parents=True, exist_ok=True)
    count = 0
    for key, task, provider, model, version, request, response, tokens_in, tokens_out, latency in conn.execute(
            "SELECT cache_key, task, provider, model, prompt_version, request, response, input_tokens, output_tokens, latency_ms "
            "FROM ops.agent_call WHERE provider NOT IN ('stub', 'replay')"):
        (target / f"{key}.json").write_text(json.dumps(
            {"task": task, "provider": provider, "model": model, "prompt_version": version, "input_tokens": tokens_in,
             "output_tokens": tokens_out, "latency_ms": latency, "request": request, "response": response}, indent=1, ensure_ascii=False), encoding="utf-8")
        count += 1
    print(json.dumps({"recordings": count, "dir": str(target)}))
    return 0


def register(sub) -> None:
    sub.add_parser("profile", help=cmd_profile.__doc__).set_defaults(func=cmd_profile)
    sub.add_parser("prompts", help=cmd_prompts.__doc__).set_defaults(func=cmd_prompts)
    agents = sub.add_parser("agents", help=cmd_agents.__doc__)
    agents.add_argument("agent", choices=["classify", "dq"])
    agents.add_argument("--task", choices=["all", *TASKS], default="all")
    agents.set_defaults(func=cmd_agents)
    sub.add_parser("proposals", help=cmd_proposals.__doc__).set_defaults(func=cmd_proposals)
    approve = sub.add_parser("approve", help=cmd_approve.__doc__)
    approve.add_argument("--task", choices=list(review.MAPS))
    approve.add_argument("--key", action="append", help="raw value to approve; repeatable")
    approve.add_argument("--all", action="store_true", help="approve every pending mapping of the task")
    approve.add_argument("--min-confidence", type=float)
    approve.add_argument("--set", help="override the proposed value while approving (category, priority)")
    approve.add_argument("--rule", action="append", type=int, help="id of a Data Quality Agent proposal; repeatable")
    approve.add_argument("--reject", action="store_true")
    approve.set_defaults(func=cmd_approve)
    ev = sub.add_parser("evals", help=cmd_evals.__doc__)
    ev.add_argument("--tasks", nargs="+", default=["category", "person", "issue", "injection", "dq"],
                    choices=["category", "person", "issue", "injection", "dq"])
    ev.add_argument("--consistency", action="store_true", help="also repeat the category task three times without the cache")
    ev.set_defaults(func=cmd_evals)
    rep = sub.add_parser("report", help=cmd_report.__doc__)
    rep.add_argument("--update-readme", action="store_true")
    rep.set_defaults(func=cmd_report)
    sub.add_parser("export-recordings", help=cmd_export_recordings.__doc__).set_defaults(func=cmd_export_recordings)
