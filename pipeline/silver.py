"""Silver: rebuild typed, standardised, flagged tickets from bronze with the approved rule set."""
import logging

import psycopg
import yaml

from . import db, rules
from .config import Settings

log = logging.getLogger("pipeline")


def _apply_settings(conn: psycopg.Connection, run_id: int, version: str, source_tz: str) -> None:
    conn.execute("SELECT set_config('app.run_id', %s, true), set_config('app.rule_set_version', %s, true), "
                 "set_config('app.source_tz', %s, true)", (str(run_id), version, source_tz))


def load_cleaning_rules(settings: Settings) -> list[dict]:
    return yaml.safe_load((settings.rules_dir / "cleaning_rules.yaml").read_text(encoding="utf-8"))


def record_dq_results(conn: psycopg.Connection, run_id: int, settings: Settings) -> list[dict]:
    """Measure the effect of every documented rule and store it in ops.dq_results."""
    totals = {
        "bronze": conn.execute("SELECT count(*) FROM bronze.tickets_raw").fetchone()[0],
        "silver": conn.execute("SELECT count(*) FROM silver.tickets").fetchone()[0],
    }
    results = []
    for rule in load_cleaning_rules(settings):
        affected = int(conn.execute(rule["measure"]).fetchone()[0])
        checked = totals[rule["scope"]]
        pct = round(100.0 * affected / checked, 3) if checked else None
        conn.execute("INSERT INTO ops.dq_results (run_id, rule_id, title, rows_checked, rows_affected, pct) VALUES (%s, %s, %s, %s, %s, %s)",
                     (run_id, rule["id"], rule["title"], checked, affected, pct))
        results.append({"id": rule["id"], "title": rule["title"], "rows_checked": checked, "rows_affected": affected})
    return results


def run(conn: psycopg.Connection, run_id: int, settings: Settings) -> dict:
    version = rules.rule_set_version(conn)
    with db.stage(conn, run_id, "silver") as counts:
        _apply_settings(conn, run_id, version, settings.source_tz)
        db.run_sql_file(conn, "20_silver.sql")
        bronze_rows, valid_rows = conn.execute(
            "SELECT count(*), count(*) FILTER (WHERE cardinality(reject_reasons) = 0) FROM _checked").fetchone()
        tickets = conn.execute("SELECT count(*) FROM silver.tickets").fetchone()[0]
        rejected = conn.execute("SELECT count(*) FROM silver.tickets_rejected").fetchone()[0]
        superseded = valid_rows - tickets          # older deliveries of a ticket that a later file replaced
        if bronze_rows != tickets + rejected + superseded or superseded < 0:
            raise AssertionError(f"reconciliation failed: bronze={bronze_rows} silver={tickets} rejected={rejected} superseded={superseded}")
        duplicates = conn.execute("SELECT count(*) FROM silver.tickets WHERE is_duplicate").fetchone()[0]
        record_dq_results(conn, run_id, settings)
        db.lineage(conn, run_id, "bronze.tickets_raw", "silver.tickets", "sql/20_silver.sql", version)
        db.lineage(conn, run_id, "bronze.tickets_raw", "silver.tickets_rejected", "sql/20_silver.sql (rule R01)", version)
        counts.update(rows_in=bronze_rows, rows_out=tickets, rows_rejected=rejected,
                      message=f"rule_set={version} duplicates_flagged={duplicates} superseded={superseded} "
                              f"current={tickets - duplicates} reconciled=bronze==silver+rejected+superseded")
    return {"rule_set_version": version, "bronze": bronze_rows, "silver": tickets, "rejected": rejected,
            "superseded": superseded, "duplicates": duplicates, "current": tickets - duplicates}
