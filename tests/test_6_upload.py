"""A delivered CSV through the console: upload it, run, see it queued for a person, approve, rebuild.
The upload and the queue are deterministic: no model is involved anywhere in this file."""
from dataclasses import replace
from pathlib import Path

from fastapi.testclient import TestClient

from conftest import run_pipeline
from pipeline import api
from test_3_console_api import console

HEADER = ("ticket_id,created_at,resolved_at,category,priority,status,building,description,"
          "submitted_by,assigned_to,resolution_notes,cost,sla_hours")
DELIVERY = "\n".join([
    HEADER,
    ('TKT-70001,2025-03-04 09:15:00,2025-03-05 10:00:00,Chiller maintenance,,Resolved,HQ Floor 1,'
     '"Chiller 2 making a loud grinding noise near the server room.",D. Kim,CityWide Electric,Replaced bearing.,350,8'),
    ('TKT-70002,2025-03-04 11:00:00,,Chiller maintenance,High,Open,Building 3,'
     '"Chiller unit vibrating, tenants complaining.",Lisa Chen,,,,8'),
    ('TKT-70003,2025-03-04 15:40:00,2025-03-06 09:00:00,Chiller maintenance,,Closed,Tower B,'
     '"Chiller 1 short cycling every few minutes.",Pat Anderson,CityWide Electric,Recharged refrigerant.,480,8'),
    ('TKT-70004,2025-03-05 08:20:00,2025-03-05 12:00:00,Deep clean,,Resolved,Tower B,'
     '"Deep clean after a water leak in the lobby.",Pat Anderson,CleanCo,Mopped and dried.,120,4'),
    ('TKT-70005,2025-03-05 09:05:00,,Deep clean,Low,Open,Annex West,'
     '"Deep clean requested for the west stairwell.",Mike Johnson,,,,4'),
    ('TKT-70006,2025-03-05 09:45:00,2025-03-05 16:30:00,Deep clean,,Closed,HQ Floor 2,'
     '"Deep clean of the cafeteria after an event.",Emily Brown,CleanCo,,,4'),
]) + "\n"
UNSEEN = ["chiller maintenance", "deep clean"]
IDS = "('TKT-70001','TKT-70002','TKT-70003','TKT-70004','TKT-70005','TKT-70006')"


def deliver(client: TestClient, name: str = "march_delivery.csv") -> dict:
    response = client.post("/files", files={"file": (name, DELIVERY.encode(), "text/csv")})
    assert response.status_code == 200, response.text
    return response.json()


def test_upload_writes_the_file_and_bronze_loads_it(clean, settings):
    uploads = Path(settings.uploads_dir)
    client = console(settings)
    before = clean.execute("SELECT count(*) FROM bronze.tickets_raw").fetchone()[0]
    result = deliver(client)

    assert (uploads / result["file"]).read_bytes() == DELIVERY.encode()
    assert result["file"].endswith("-march_delivery.csv") and len(result["sha256"]) == 12

    summary = run_pipeline(conn=clean, settings=settings)
    assert summary["bronze"] == before + 6
    landed = clean.execute(f"SELECT count(*) FROM bronze.tickets_raw WHERE ticket_id IN {IDS}").fetchone()[0]
    assert landed == 6

    queued = clean.execute("SELECT raw_key, category_std, status, source FROM silver.category_map "
                           "WHERE raw_key = ANY (%s) ORDER BY raw_key", (UNSEEN,)).fetchall()
    assert queued == [(key, "Unclassified", "proposed", "unseen") for key in UNSEEN]
    flagged = clean.execute(f"SELECT count(*) FROM silver.tickets WHERE ticket_id IN {IDS} "
                            "AND 'category_unmapped' = ANY (dq_flags)").fetchone()[0]
    assert flagged == 6


def test_upload_rejects_what_a_delivery_is_not(clean, settings, monkeypatch):
    client = console(settings)
    assert client.post("/files", files={"file": ("notes.txt", b"hello", "text/plain")}).status_code == 400
    assert client.post("/files", files={"file": ("empty.csv", b"  \n", "text/csv")}).status_code == 400
    monkeypatch.setattr(api, "MAX_UPLOAD_BYTES", 48)
    assert client.post("/files", files={"file": ("big.csv", b"x" * 64, "text/csv")}).status_code == 413
    assert not any(path.name.endswith("-big.csv") for path in Path(settings.uploads_dir).iterdir())


def test_a_rare_unmapped_value_is_flagged_but_not_queued(clean, settings):
    # one stray value: below classify_min_rows, the same threshold the classifier applies to a candidate list
    stray = "\n".join([HEADER,
                       'TKT-70011,2025-03-05 10:00:00,,Basement flood check,,Open,Tower A,'
                       '"Checking the basement after heavy rain.",Mike Johnson,,,,4']) + "\n"
    client = console(settings)
    response = client.post("/files", files={"file": ("stray.csv", stray.encode(), "text/csv")})
    assert response.status_code == 200
    run_pipeline(conn=clean, settings=settings)
    assert clean.execute("SELECT count(*) FROM silver.category_map WHERE raw_key = 'basement flood check'").fetchone()[0] == 0
    assert clean.execute("SELECT count(*) FROM silver.tickets WHERE ticket_id = 'TKT-70011' "
                         "AND 'category_unmapped' = ANY (dq_flags)").fetchone()[0] == 1


def test_approval_with_a_corrected_category_applies_on_the_next_run(clean, settings):
    client = console(settings)
    deliver(client)
    run_pipeline(conn=clean, settings=settings)          # the first run queues the unseen values
    before = clean.execute("SELECT rule_set_version FROM ops.pipeline_run ORDER BY run_id DESC LIMIT 1").fetchone()[0]
    clean.rollback()

    decision = client.post("/review/mappings", json={"task": "category", "keys": ["chiller maintenance"],
                                                     "decision": "approve", "set_value": "HVAC"})
    assert decision.status_code == 200 and decision.json()["changed"] == 1

    summary = run_pipeline(conn=clean, settings=settings)
    assert summary["rule_set_version"] != before
    row = clean.execute("SELECT category_std, _rule_set_version FROM silver.tickets WHERE ticket_id = 'TKT-70001'").fetchone()
    assert row == ("HVAC", summary["rule_set_version"])
    assert clean.execute(f"SELECT count(*) FROM silver.tickets WHERE ticket_id IN {IDS} "
                         "AND 'category_unmapped' = ANY (dq_flags)").fetchone()[0] == 3     # 'deep clean' still awaits a person
    still_queued = clean.execute("SELECT status FROM silver.category_map WHERE raw_key = 'deep clean'").fetchone()[0]
    assert still_queued == "proposed"
