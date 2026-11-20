"""Tests run against a real PostgreSQL: a scratch database created next to the configured one and dropped afterwards."""
import shutil
import sys
from dataclasses import replace
from pathlib import Path

import psycopg
import pytest
from psycopg import sql

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

from pipeline import bronze, config, db, gold, rules, silver  # noqa: E402


@pytest.fixture(scope="session")
def settings(tmp_path_factory):
    base = config.load()
    info = psycopg.conninfo.conninfo_to_dict(base.database_url)
    scratch = f"{info['dbname']}_pytest"
    with psycopg.connect(base.database_url, autocommit=True) as admin:
        admin.execute(sql.SQL("DROP DATABASE IF EXISTS {}").format(sql.Identifier(scratch)))
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(scratch)))
    data_dir = tmp_path_factory.mktemp("data")
    shutil.copy(ROOT / "data" / "raw_tickets.csv", data_dir / "raw_tickets.csv")
    yield replace(base, database_url=psycopg.conninfo.make_conninfo(base.database_url, dbname=scratch), data_dir=data_dir,
                  uploads_dir=tmp_path_factory.mktemp("uploads"),
                  reports_dir=tmp_path_factory.mktemp("reports"), llm_provider="none",
                  replay_dir=tmp_path_factory.mktemp("recordings"))
    with psycopg.connect(base.database_url, autocommit=True) as admin:
        admin.execute(sql.SQL("DROP DATABASE IF EXISTS {} WITH (FORCE)").format(sql.Identifier(scratch)))


def run_pipeline(conn, settings) -> dict:
    run_id = db.start_run(conn, "test")
    bronze.run(conn, run_id, settings.data_dir, settings.uploads_dir)
    summary = silver.run(conn, run_id, settings)
    summary["gold"] = gold.run(conn, run_id, summary["rule_set_version"])
    db.finish_run(conn, run_id, "succeeded", rule_set_version=summary["rule_set_version"])
    return summary


@pytest.fixture(scope="session")
def loaded(settings):
    """A connection to a database where the supplied file has been through the full pipeline once."""
    conn = db.connect(settings)
    db.migrate(conn)
    rules.load_seeds(conn, settings.rules_dir)
    summary = run_pipeline(conn, settings)
    yield conn, summary
    conn.close()


@pytest.fixture()
def conn(loaded):
    connection, _ = loaded
    yield connection
    connection.rollback()


@pytest.fixture()
def clean(loaded, settings):
    """Give each test the seeded state back, whatever it did to the mapping tables."""
    conn, _ = loaded
    yield conn
    conn.rollback()
    for table in ("silver.category_map", "silver.person_map", "silver.issue_map", "silver.priority_map"):
        conn.execute(f"DELETE FROM {table} WHERE source <> 'seed' OR status <> 'approved'")
    conn.execute("DELETE FROM ops.rule_proposal")
    conn.execute("DELETE FROM ops.agent_call")
    conn.commit()
    rules.load_seeds(conn, settings.rules_dir)
    run_pipeline(conn, settings)
