"""Database helpers: connection, SQL files, and the run / stage log."""
import logging
import time
from contextlib import contextmanager
from datetime import datetime, timezone

import psycopg

from .config import ROOT, Settings

log = logging.getLogger("pipeline")
SQL_DIR = ROOT / "sql"


def connect(settings: Settings, retries: int = 30) -> psycopg.Connection:
    """Connect, waiting for the database to accept connections (useful under docker compose)."""
    last_error = None
    for _ in range(retries):
        try:
            conn = psycopg.connect(settings.database_url)
            conn.execute("SET TIME ZONE 'UTC'")
            conn.commit()
            return conn
        except psycopg.OperationalError as exc:
            last_error = exc
            time.sleep(1)
    raise RuntimeError(f"database not reachable: {last_error}")


def run_sql_file(conn: psycopg.Connection, name: str) -> None:
    conn.execute((SQL_DIR / name).read_text(encoding="utf-8"))


def migrate(conn: psycopg.Connection) -> None:
    """Create schemas, tables and functions if they do not exist."""
    with conn.cursor() as cur:
        cur.execute("SET LOCAL client_min_messages = warning")
        cur.execute((SQL_DIR / "00_schemas.sql").read_text(encoding="utf-8"))
    conn.commit()


def start_run(conn: psycopg.Connection, command: str) -> int:
    run_id = conn.execute("INSERT INTO ops.pipeline_run (command) VALUES (%s) RETURNING run_id", (command,)).fetchone()[0]
    conn.commit()
    log.info("run=%s started command=%s", run_id, command)
    return run_id


def finish_run(conn: psycopg.Connection, run_id: int, status: str, message: str = "", rule_set_version: str | None = None) -> None:
    conn.rollback()  # leave no half-finished stage behind
    conn.execute(
        "UPDATE ops.pipeline_run SET finished_at = now(), status = %s, message = %s, "
        "rule_set_version = coalesce(%s, rule_set_version) WHERE run_id = %s",
        (status, message[:2000], rule_set_version, run_id),
    )
    conn.commit()
    log.info("run=%s finished status=%s %s", run_id, status, message)


@contextmanager
def stage(conn: psycopg.Connection, run_id: int, name: str):
    """One pipeline stage = one transaction. Counts set on the yielded dict are written to ops.stage_log."""
    started = datetime.now(timezone.utc)
    counts: dict = {"rows_in": None, "rows_out": None, "rows_rejected": None, "message": ""}
    try:
        yield counts
        conn.commit()
        status = "succeeded"
    except Exception as exc:
        conn.rollback()
        status = "failed"
        counts["message"] = f"{type(exc).__name__}: {exc}"
        raise
    finally:
        conn.execute(
            "INSERT INTO ops.stage_log (run_id, stage, started_at, finished_at, rows_in, rows_out, rows_rejected, status, message) "
            "VALUES (%s, %s, %s, now(), %s, %s, %s, %s, %s)",
            (run_id, name, started, counts["rows_in"], counts["rows_out"], counts["rows_rejected"], status, str(counts["message"])[:2000]),
        )
        conn.commit()
        log.info("run=%s stage=%s status=%s rows_in=%s rows_out=%s rows_rejected=%s %s", run_id, name, status,
                 counts["rows_in"], counts["rows_out"], counts["rows_rejected"], counts["message"])


def lineage(conn: psycopg.Connection, run_id: int, source: str, target: str, transformation: str, rule_set_version: str | None = None) -> None:
    conn.execute(
        "INSERT INTO ops.lineage_edge (run_id, source_object, target_object, transformation, rule_set_version) VALUES (%s, %s, %s, %s, %s)",
        (run_id, source, target, transformation, rule_set_version),
    )
