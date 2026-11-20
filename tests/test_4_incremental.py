"""A second delivery and a changed header. These tests change the scratch database, so they run last."""
import datetime as dt
import shutil
from dataclasses import replace
from decimal import Decimal

import pytest

from agents import classifier, review
from agents.llm import LLM
from conftest import ROOT, run_pipeline
from pipeline import bronze, checks, db
from stub_model import StubModel

FIXTURES = ROOT / "tests" / "fixtures"


def test_changed_header_blocks_the_file_and_is_recorded(loaded, tmp_path):
    conn, _ = loaded
    shutil.copy(FIXTURES / "drifted_header.csv", tmp_path / "drifted_header.csv")
    before = conn.execute("SELECT count(*) FROM bronze.tickets_raw").fetchone()[0]
    run_id = db.start_run(conn, "test-drift")
    with pytest.raises(bronze.SchemaDrift, match="sla_hrs"):
        bronze.run(conn, run_id, tmp_path)
    db.finish_run(conn, run_id, "failed", "schema drift")
    assert conn.execute("SELECT count(*) FROM bronze.tickets_raw").fetchone()[0] == before          # nothing was loaded
    kind, detail = conn.execute("SELECT kind, detail FROM ops.drift_event WHERE run_id = %s", (run_id,)).fetchone()
    assert kind == "header_changed" and detail["added"] == ["sla_hrs", "region"] and detail["missing"] == ["sla_hours"]
    assert conn.execute("SELECT status FROM ops.stage_log WHERE run_id = %s AND stage = 'bronze'", (run_id,)).fetchone()[0] == "failed"
    assert conn.execute("SELECT count(*) FROM ops.file_manifest").fetchone()[0] == 1


def test_second_delivery_is_incremental_and_flags_what_it_cannot_map(loaded, settings):
    conn, first = loaded
    shutil.copy(FIXTURES / "day2_tickets.csv", settings.data_dir / "day2_tickets.csv")
    summary = run_pipeline(conn, settings)

    # bronze: only the four new rows were added; the first file was skipped by its hash
    assert summary["bronze"] == first["bronze"] + 4
    assert conn.execute("SELECT count(*) FROM ops.file_manifest").fetchone()[0] == 2
    # silver: one updated ticket (older version superseded), two new tickets, one new row quarantined
    assert summary["superseded"] == 1 and summary["silver"] == first["silver"] + 2 and summary["rejected"] == first["rejected"] + 1
    assert summary["bronze"] == summary["silver"] + summary["rejected"] + summary["superseded"]

    # the later delivery of TKT-3960 wins; both versions stay in bronze
    assert conn.execute("SELECT count(*) FROM bronze.tickets_raw WHERE ticket_id = 'TKT-3960'").fetchone()[0] == 2
    status, resolved, cost, hours = conn.execute(
        "SELECT status, resolved_at, cost_amount, resolution_hours FROM silver.tickets WHERE ticket_id = 'TKT-3960'").fetchone()
    assert (status, resolved, cost) == ("Closed", dt.datetime(2024, 10, 25, 9, 0), Decimal("250.00")) and hours == Decimal("18.46")

    # an unseen category spelling does not block the load: Unclassified and flagged
    assert conn.execute("SELECT category_std, 'category_unmapped' = ANY (dq_flags) FROM silver.tickets WHERE ticket_id = 'TKT-20001'").fetchone() == ("Unclassified", True)
    # a known spelling in the same file maps as usual; an unseen submitter spelling is flagged, not guessed
    assert conn.execute("SELECT category_std, submitter_id, 'submitter_unmapped' = ANY (dq_flags) FROM silver.tickets WHERE ticket_id = 'TKT-20003'").fetchone() == ("Elevator", None, True)
    # an unregistered timestamp format is quarantined for exactly one reason, which is the drift signal
    assert conn.execute("SELECT reasons FROM silver.tickets_rejected WHERE raw->>'ticket_id' = 'TKT-20002'").fetchone()[0] == ["unparseable_created_at"]

    run_id = conn.execute("SELECT max(run_id) FROM ops.pipeline_run").fetchone()[0]
    assert checks.run(conn, run_id, settings) == "warn"
    alerts = {r[0] for r in conn.execute("SELECT signal FROM ops.alert WHERE run_id = %s", (run_id,))}
    assert alerts == {"single_reason_rejects", "unmapped_rows_pct"}
    assert conn.execute("SELECT count(*) FROM ops.drift_event WHERE run_id = %s", (run_id,)).fetchone()[0] == 2

    # running again changes nothing
    again = run_pipeline(conn, settings)
    assert {k: again[k] for k in ("bronze", "silver", "rejected", "superseded", "duplicates")} == {k: summary[k] for k in ("bronze", "silver", "rejected", "superseded", "duplicates")}


def test_unseen_values_go_through_the_agent_and_the_approval_gate(loaded, settings):
    conn, _ = loaded
    # one row is below the default frequency threshold, so by default the value is held and costs nothing
    items, held = classifier.category_candidates(conn, settings.classify_min_rows)
    assert items == [] and held == ["aircon"]
    eager = replace(settings, classify_min_rows=1)
    items, held = classifier.category_candidates(conn, eager.classify_min_rows)
    assert [i.value for i in items] == ["aircon"]
    outcome = classifier.classify_category(LLM(eager, conn, transport=StubModel()), eager, items)   # the stub answers "unknown"
    assert classifier.store(conn, eager, outcome) == {"provisional": 0, "proposed": 1}
    assert classifier.category_candidates(conn, 1) == ([], [])          # now on file: it is not sent a second time
    # the reviewer supplies the right category while approving
    assert review.approve_mappings(conn, "category", "reviewer", keys=["aircon"], set_value="HVAC") == 1
    run_pipeline(conn, settings)
    assert conn.execute("SELECT category_std, dq_flags FROM silver.tickets WHERE ticket_id = 'TKT-20001'").fetchone() == ("HVAC", [])
