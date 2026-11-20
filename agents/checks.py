"""Approved Data Quality Agent proposals run as checks on every load. Results land in ops.dq_results as A:<rule_id>."""
import logging

import psycopg

from pipeline.config import Settings

from .guard import ReadOnlyRunner, UnsafeSQL

log = logging.getLogger("agents")


def run_approved(conn: psycopg.Connection, run_id: int, settings: Settings) -> int:
    rules = conn.execute("SELECT rule_id, finding, sql_kind, sql FROM ops.rule_proposal WHERE status = 'approved' ORDER BY id").fetchall()
    if not rules:
        return 0
    total = conn.execute("SELECT count(*) FROM bronze.tickets_raw").fetchone()[0]
    runner = ReadOnlyRunner(settings)
    try:
        for rule_id, finding, kind, sql in rules:
            try:
                affected = runner.count(sql, kind)
            except (UnsafeSQL, psycopg.Error) as exc:
                log.warning("run=%s agent check %s failed: %s", run_id, rule_id, str(exc).splitlines()[0])
                continue
            conn.execute(
                "INSERT INTO ops.dq_results (run_id, rule_id, title, rows_checked, rows_affected, pct) VALUES (%s, %s, %s, %s, %s, %s) "
                "ON CONFLICT (run_id, rule_id) DO NOTHING",
                (run_id, f"A:{rule_id}"[:80], finding[:200], total, affected, round(100.0 * affected / total, 3) if total else None))
    finally:
        runner.close()
    conn.commit()
    return len(rules)
