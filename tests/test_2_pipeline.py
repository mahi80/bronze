"""The full pipeline on the supplied file: counts, reconciliation, idempotency, and specific tickets."""
import csv
import datetime as dt
from decimal import Decimal

from conftest import ROOT, run_pipeline
from pipeline import bronze


def table_checksum(conn, table: str, order_by: str) -> str:
    """md5 over every column except the run stamp, so two builds of the same input can be compared."""
    schema, name = table.split(".")
    columns = [r[0] for r in conn.execute(
        "SELECT column_name FROM information_schema.columns WHERE table_schema = %s AND table_name = %s "
        "AND column_name NOT IN ('_run_id', '_built_at') ORDER BY ordinal_position", (schema, name))]
    return conn.execute(f"SELECT md5(string_agg(md5(row_text), '' ORDER BY ord)) FROM (SELECT ({', '.join(columns)})::text AS row_text, "
                        f"({order_by})::text AS ord FROM {table}) t").fetchone()[0]


def test_counts_reconcile(loaded):
    _, summary = loaded
    assert summary["bronze"] == 10280
    assert summary["rejected"] == 30
    assert summary["silver"] == 10250
    assert summary["superseded"] == 0
    assert summary["duplicates"] == 250
    assert summary["current"] == 10000
    assert summary["bronze"] == summary["silver"] + summary["rejected"] + summary["superseded"]


def test_bronze_is_the_file_exactly(conn):
    with (ROOT / "data" / "raw_tickets.csv").open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        next(reader)
        expected = {bronze.row_hash(fields) for fields in reader}
    stored = {row[0] for row in conn.execute("SELECT _row_hash FROM bronze.tickets_raw")}
    assert stored == expected and len(stored) == 10280
    # placeholders are kept as text, not interpreted
    assert conn.execute("SELECT count(*) FROM bronze.tickets_raw WHERE cost = 'N/A'").fetchone()[0] == 494
    assert conn.execute("SELECT count(*) FROM bronze.tickets_raw WHERE description = ''").fetchone()[0] == 117


def test_columns_are_tagged_when_the_file_lands(conn):
    tags = dict(conn.execute("SELECT column_name, semantic_type || ' / ' || sensitivity FROM ops.column_tags"))
    assert len(tags) == 13
    assert tags["submitted_by"] == "person_name / personal_data" and tags["cost"] == "money / commercially_sensitive"
    assert tags["created_at"] == "timestamp / none" and tags["description"] == "free_text / may_contain_personal_data"
    stage = conn.execute("SELECT message FROM ops.stage_log WHERE stage = 'bronze' ORDER BY started_at LIMIT 1").fetchone()[0]
    assert "tagged_columns=13" in stage


def test_rerun_is_idempotent(loaded, settings):
    conn, first = loaded
    tables = {"silver.tickets": "ticket_id", "silver.tickets_rejected": "_row_hash",
              "gold.sla_performance_monthly": "month || category || priority",
              "gold.building_category_hotspots": "building || category", "gold.assignee_workload_cost": "assignee || month"}
    before = {t: table_checksum(conn, t, o) for t, o in tables.items()}
    bronze_before = conn.execute("SELECT count(*), max(_ingested_at) FROM bronze.tickets_raw").fetchone()
    second = run_pipeline(conn, settings)
    assert {t: table_checksum(conn, t, o) for t, o in tables.items()} == before
    assert conn.execute("SELECT count(*), max(_ingested_at) FROM bronze.tickets_raw").fetchone() == bronze_before
    assert conn.execute("SELECT count(*) FROM ops.file_manifest").fetchone()[0] == 1
    assert second["rule_set_version"] == first["rule_set_version"]
    assert {k: second[k] for k in ("silver", "rejected", "duplicates", "current")} == {k: first[k] for k in ("silver", "rejected", "duplicates", "current")}


def test_quarantined_rows_fail_several_checks(conn):
    reasons = conn.execute("SELECT min(cardinality(reasons)), count(*) FROM silver.tickets_rejected").fetchone()
    assert reasons == (3, 30)        # none is a real ticket caught by a single rule


def test_every_base_ticket_survives_deduplication(conn):
    survivors, flagged_base, dangling = conn.execute(
        """SELECT count(*) FILTER (WHERE NOT is_duplicate),
                  count(*) FILTER (WHERE is_duplicate AND ticket_number BETWEEN 1000 AND 10999),
                  count(*) FILTER (WHERE is_duplicate AND duplicate_of NOT IN (SELECT ticket_id FROM silver.tickets WHERE NOT is_duplicate))
           FROM silver.tickets""").fetchone()
    assert (survivors, flagged_base, dangling) == (10000, 0, 0)
    strengths = dict(conn.execute("SELECT dup_match_strength, count(*) FROM silver.tickets WHERE is_duplicate GROUP BY 1"))
    assert strengths == {"strong": 223, "weak": 3, "truncated_prefix": 24}


def test_swap_repair_and_truncation(conn):
    swapped, truncated = conn.execute("SELECT count(*) FILTER (WHERE 'swapped_columns' = ANY (dq_flags)), "
                                      "count(*) FILTER (WHERE 'description_truncated' = ANY (dq_flags)) FROM silver.tickets").fetchone()
    assert (swapped, truncated) == (50, 24)
    row = conn.execute("SELECT category_std, category_raw, description FROM silver.tickets WHERE ticket_id = 'TKT-11268'").fetchone()
    assert row == ("Electrical", "Elec", "need more outlets in conf room 387")
    # a description that is naturally 60 characters long is a plain duplicate, not a truncated one
    flags, strength = conn.execute("SELECT dq_flags, dup_match_strength FROM silver.tickets WHERE ticket_id = 'TKT-11249'").fetchone()
    assert "description_truncated" not in flags and strength == "strong"
    assert conn.execute("SELECT count(*) FROM silver.tickets WHERE category_std = 'Unclassified'").fetchone()[0] == 0


def test_specific_tickets(conn):
    def ticket(ticket_id, columns):
        return conn.execute(f"SELECT {columns} FROM silver.tickets WHERE ticket_id = %s", (ticket_id,)).fetchone()

    # reversed timestamps: kept, flagged, no duration
    created, resolved, hours, flags = ticket("TKT-5149", "created_at, resolved_at, resolution_hours, dq_flags")
    assert created == dt.datetime(2024, 6, 16, 4, 31, 56) and resolved == dt.datetime(2024, 3, 14, 0, 39, 27)
    assert hours is None and "resolved_before_created" in flags and "resolved_at_on_open_ticket" in flags
    # currency symbol removed; sentinel cost and SLA become NULL with a status
    assert ticket("TKT-3097", "cost_amount, cost_status") == (Decimal("3065.86"), "known")
    assert ticket("TKT-1579", "cost_amount, cost_status") == (None, "unknown")
    assert ticket("TKT-8121", "sla_hours, sla_status") == (None, "placeholder")
    assert ticket("TKT-4908", "cost_amount, cost_status, sla_status") == (None, "not_applicable", "not_set")
    # epoch seconds, month-first US date, name resolution, priority mapping
    assert ticket("TKT-10308", "created_at, created_at_format") == (dt.datetime(2024, 9, 17, 4, 36, 8), "epoch_s")
    assert ticket("TKT-2995", "created_at, created_at_precision") == (dt.datetime(2025, 2, 3), "day")
    assert ticket("TKT-9771", "submitter_name, submitted_by_raw, priority_std") == ("Robert Martinez", "Bob Martinez", "Medium")
    assert ticket("TKT-10991", "priority_std, priority_raw") == ("Medium", "Normal")
    # resolution notes parsed by pattern
    assert ticket("TKT-3752", "note_type, root_cause") == ("root_cause", "power surge")
    assert ticket("TKT-3108", "note_type, duplicate_of_ref") == ("duplicate_reference", "TKT-3098")
    assert ticket("TKT-7335", "note_type, hours_on_site") == ("completed", Decimal("2.7"))
    # description enrichment
    assert ticket("TKT-5543", "asset_id, issue_type, hazard_signal") == ("H-011", "elevator stuck", "service_outage")
    assert ticket("TKT-9025", "hazard_signal, category_std") == ("safety_hazard", "Electrical")


def test_gold_models(conn):
    total = conn.execute("SELECT count(*) FROM silver.tickets_current").fetchone()[0]
    for model, column in [("gold.sla_performance_monthly", "tickets_opened"), ("gold.building_category_hotspots", "tickets"),
                          ("gold.assignee_workload_cost", "tickets")]:
        assert conn.execute(f"SELECT sum({column}) FROM {model}").fetchone()[0] == total
    assert conn.execute("SELECT count(*), count(DISTINCT building), count(DISTINCT category) FROM gold.building_category_hotspots").fetchone() == (170, 17, 10)
    measurable, within, bad = conn.execute(
        "SELECT sum(tickets_measurable), sum(tickets_within_sla), count(*) FILTER (WHERE pct_measurable > 100 OR tickets_within_sla > tickets_measurable) "
        "FROM gold.sla_performance_monthly").fetchone()
    assert within <= measurable <= total and bad == 0
    assert conn.execute("SELECT count(*) FROM gold.assignee_workload_cost WHERE assignee = '(unassigned)'").fetchone()[0] > 0


def test_every_rule_is_measured_and_lineage_recorded(conn):
    run_id = conn.execute("SELECT max(run_id) FROM ops.pipeline_run WHERE status = 'succeeded'").fetchone()[0]
    measured = dict(conn.execute("SELECT rule_id, rows_affected FROM ops.dq_results WHERE run_id = %s", (run_id,)))
    assert measured["R01"] == 30 and measured["R02"] == 50 and measured["R03"] == 250 and measured["R03b"] == 24
    assert measured["R06"] == 1865 and measured["R07"] == 2127 and measured["R07a"] == 100
    assert measured["R08a"] == 0 and measured["R13a"] == 0 and measured["R17"] == 0 and measured["R17a"] == 0
    stages = [r[0] for r in conn.execute("SELECT stage FROM ops.stage_log WHERE run_id = %s ORDER BY started_at", (run_id,))]
    assert stages == ["bronze", "silver", "gold"]
    targets = {r[0] for r in conn.execute("SELECT target_object FROM ops.lineage_edge WHERE run_id = %s", (run_id,))}
    assert {"silver.tickets", "silver.tickets_rejected", "gold.sla_performance_monthly"} <= targets
    # row-level lineage: every silver row points at exactly one bronze row
    assert conn.execute("SELECT count(*) FROM silver.tickets s JOIN bronze.tickets_raw b USING (_row_hash)").fetchone()[0] == 10250
