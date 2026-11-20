"""Command line entry point: python -m pipeline <command>."""
import argparse
import json
import logging
import sys

from agents import checks as agent_checks

from . import bronze, checks, config, db, gold, rules, silver

log = logging.getLogger("pipeline")
RUN_LOCK = 20261118  # PostgreSQL advisory lock: one run at a time, from the command line or the console


class RunInProgress(RuntimeError):
    """Another run holds the lock."""


class RunFailed(RuntimeError):
    """A stage failed. The run is recorded as failed; the previous silver and gold stay in place."""

    def __init__(self, run_id: int, message: str):
        super().__init__(message)
        self.run_id = run_id


def run_once(conn, settings) -> dict:
    """bronze -> silver -> gold, then threshold checks. Safe to run again at any time."""
    if not conn.execute("SELECT pg_try_advisory_lock(%s)", (RUN_LOCK,)).fetchone()[0]:
        conn.rollback()
        raise RunInProgress("another pipeline run is in progress")
    try:
        db.migrate(conn)
        rules.load_seeds(conn, settings.rules_dir)
        run_id = db.start_run(conn, "run")
        try:
            bronze.run(conn, run_id, settings.data_dir)
            summary = silver.run(conn, run_id, settings)
            summary["agent_checks"] = agent_checks.run_approved(conn, run_id, settings)
            summary["gold"] = gold.run(conn, run_id, summary["rule_set_version"])
            level = checks.run(conn, run_id, settings)
        except Exception as exc:
            db.finish_run(conn, run_id, "failed", f"{type(exc).__name__}: {exc}")
            log.error("run=%s failed: %s", run_id, exc)
            raise RunFailed(run_id, f"{type(exc).__name__}: {exc}") from exc
        db.finish_run(conn, run_id, "succeeded", f"checks={level}", summary["rule_set_version"])
        return {"run_id": run_id, "checks": level, **summary}
    finally:
        conn.rollback()
        conn.execute("SELECT pg_advisory_unlock(%s)", (RUN_LOCK,))
        conn.commit()


def cmd_run(args, settings) -> int:
    """bronze -> silver -> gold, then threshold checks. Safe to run again at any time."""
    try:
        result = run_once(db.connect(settings), settings)
    except RunInProgress as exc:
        log.error("%s", exc)
        return 4
    except RunFailed:
        return 1
    print(json.dumps(result, indent=2))
    return 2 if result["checks"] == "fail" else 0


def cmd_reset(args, settings) -> int:
    """Drop every pipeline schema. For tests and demos only."""
    conn = db.connect(settings)
    conn.execute("DROP SCHEMA IF EXISTS gold, silver, bronze, ops CASCADE")
    conn.commit()
    log.info("schemas dropped")
    return 0


def cmd_export_rules(args, settings) -> int:
    conn = db.connect(settings)
    print(json.dumps(rules.export_approved(conn, settings.rules_dir), indent=2))
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="pipeline", description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("run", help=cmd_run.__doc__).set_defaults(func=cmd_run)
    sub.add_parser("reset", help=cmd_reset.__doc__).set_defaults(func=cmd_reset)
    sub.add_parser("export-rules", help="write approved mappings back to rules/*.csv").set_defaults(func=cmd_export_rules)
    # agent commands are registered by the agents package, which is optional at run time
    from agents import cli as agents_cli
    agents_cli.register(sub)
    return parser


def main(argv=None) -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s", stream=sys.stderr)
    args = build_parser().parse_args(argv)
    return args.func(args, config.load())


if __name__ == "__main__":
    sys.exit(main())
