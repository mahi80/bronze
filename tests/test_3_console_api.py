"""The console API: the pipeline, the approval gate and the agents reached over HTTP. It must add nothing of its own,
so these tests check that what it reports and does is what the command line reports and does. Agents run on the stub."""
import shutil
import time
from dataclasses import replace

import psycopg
from fastapi.testclient import TestClient

from pipeline import api, cli
from stub_model import StubModel
from test_3_agents import DQ_SCRIPT

TOKEN = "test-token-not-a-secret"
READ_ROUTES = ["/overview", "/runs", "/runs/1", "/quality", "/gold/sla_performance_monthly", "/tickets/TKT-1000", "/review",
               "/agents", "/agents/job"]


def console(settings, transport=None) -> TestClient:
    app = api.create_app(replace(settings, console_api_token=TOKEN), transport=transport)
    return TestClient(app, headers={"Authorization": f"Bearer {TOKEN}"})


def read(conn, sql: str, params: tuple = ()) -> list[tuple]:
    """Read on the shared test connection and end the transaction: the API works on its own connections and its
    rebuild must not wait behind a lock this one still holds."""
    result = conn.execute(sql, params).fetchall()
    conn.rollback()
    return result


def wait_for_job(client: TestClient) -> dict:
    for _ in range(300):
        job = client.get("/agents/job").json()
        if job["status"] != "running":
            return job
        time.sleep(0.1)
    raise AssertionError("the agent job did not finish")


def test_only_health_answers_without_the_token(clean, settings):
    anonymous = TestClient(api.create_app(replace(settings, console_api_token=TOKEN)))
    assert anonymous.get("/health").json() == {"status": "ok"}
    assert {anonymous.get(path).status_code for path in READ_ROUTES} == {401}
    assert {anonymous.post(path, json={}).status_code for path in ("/runs", "/review/mappings", "/review/rules", "/agents/dq")} == {401}
    assert anonymous.get("/overview", headers={"Authorization": "Bearer wrong"}).status_code == 401
    # with no token configured the API serves nothing, rather than serving everyone
    unconfigured = TestClient(api.create_app(replace(settings, console_api_token="")))
    assert unconfigured.get("/overview", headers={"Authorization": "Bearer "}).status_code == 503


def test_overview_shows_the_row_accounting_the_pipeline_asserted(clean, settings):
    body = console(settings).get("/overview").json()
    counts = body["accounting"]
    assert (counts["bronze"], counts["silver"], counts["rejected"], counts["superseded"], counts["duplicates"]) == (10280, 10250, 30, 0, 250)
    assert {s["signal"]: s["level"] for s in body["signals"]} == {
        "reject_rate_pct": "ok", "single_reason_rejects": "ok", "duplicate_rate_pct": "ok", "unmapped_rows_pct": "ok", "unmatched_notes": "ok"}
    assert {g["model"]: g["rows"] for g in body["gold"]} == {
        "sla_performance_monthly": 901, "building_category_hotspots": 170, "assignee_workload_cost": 146}
    assert body["pending"] == {"mappings": 0, "rules": 0} and body["rule_set_version"] == body["built_with"]


def test_a_run_from_the_console_is_the_same_idempotent_run(clean, settings):
    client = console(settings)
    # everything in silver except the run that built it
    content = ("SELECT md5(string_agg((to_jsonb(t) - '_run_id')::text, '' ORDER BY ticket_id)) FROM silver.tickets t")
    before = read(clean, content)
    result = client.post("/runs").json()
    assert (result["status"], result["checks"], result["silver"], result["current"]) == ("succeeded", "ok", 10250, 10000)
    assert read(clean, content) == before and read(clean, "SELECT count(*) FROM bronze.tickets_raw") == [(10280,)]
    assert read(clean, "SELECT DISTINCT _run_id FROM silver.tickets") == [(result["run_id"],)]      # rebuilt by this run

    detail = client.get(f"/runs/{result['run_id']}").json()
    assert detail["run"]["status"] == "succeeded" and [s["stage"] for s in detail["stages"]] == ["bronze", "silver", "gold"]
    assert len(detail["rules"]) == 28 and detail["alerts"] == [] and len(detail["lineage"]) == 5
    assert client.get("/runs").json()["runs"][0]["run_id"] == result["run_id"]
    assert client.get("/runs/999999").status_code == 404

    # one run at a time: while another session holds the lock, the console is told so and nothing starts
    with psycopg.connect(settings.database_url, autocommit=True) as other:
        other.execute("SELECT pg_advisory_lock(%s)", (cli.RUN_LOCK,))
        runs = read(clean, "SELECT count(*) FROM ops.pipeline_run")
        assert client.post("/runs").status_code == 409
        assert read(clean, "SELECT count(*) FROM ops.pipeline_run") == runs


def test_quality_gold_and_ticket_trace_read_what_the_pipeline_wrote(clean, settings):
    client = console(settings)
    quality = client.get("/quality").json()
    effects = {r["id"]: r["rows_affected"] for r in quality["rules"]}
    assert len(effects) == 28 and (effects["R01"], effects["R02"], effects["R03"], effects["R06"]) == (30, 50, 250, 1865)
    assert len(quality["rejected"]) == 30 and {d["strength"]: d["rows"] for d in quality["duplicates"]} == {"strong": 223, "truncated_prefix": 24, "weak": 3}
    assert [t["column_name"] for t in quality["tags"]][:2] == ["ticket_id", "created_at"] and len(quality["tags"]) == 13

    for model, total in (("sla_performance_monthly", 901), ("building_category_hotspots", 170), ("assignee_workload_cost", 146)):
        body = client.get(f"/gold/{model}").json()
        assert body["total"] == total == len(body["rows"]) and "_built_at" not in body["rows"][0]
    # only the three gold models are served: the table name never comes from the request
    assert {client.get(f"/gold/{name}").status_code for name in ("pg_user", "tickets_raw", "sla_performance_monthly;select 1")} == {404}

    original = client.get("/tickets/tkt-7113").json()                       # an original with one re-keyed copy
    assert original["silver"]["is_duplicate"] is False and original["copies"] == [{"ticket_id": "TKT-11000", "dup_match_strength": "strong"}]
    assert original["bronze"][0]["_row_hash"] == original["silver"]["_row_hash"]             # silver points at its bronze row
    copy = client.get("/tickets/TKT-11231").json()                          # a swapped, truncated copy
    assert copy["silver"]["duplicate_of"] == "TKT-7386" and "swapped_columns" in copy["silver"]["dq_flags"]
    assert copy["bronze"][0]["category"] != copy["silver"]["category_raw"]                   # bronze keeps the swap as delivered
    quarantined = client.get("/tickets/15").json()                          # a placeholder row: in bronze, not in silver
    assert quarantined["silver"] is None and len(quarantined["bronze"][0]["reject_reasons"]) == 4
    assert client.get("/tickets/TKT-999999").status_code == 404


def test_mappings_are_approved_by_exact_key_and_only_then_change_silver(clean, settings, tmp_path):
    # a rule set that has never seen three spellings: they are in neither the seed file nor the database
    unseen = ["lift", "a/c", "exterminator"]
    shutil.copytree(settings.rules_dir, tmp_path / "rules")
    seed = tmp_path / "rules" / "category_map.csv"
    seed.write_text("".join(line for line in seed.read_text(encoding="utf-8").splitlines(keepends=True) if line.split(",")[0] not in unseen),
                    encoding="utf-8")
    settings = replace(settings, rules_dir=tmp_path / "rules")
    conn = clean
    conn.execute("DELETE FROM silver.category_map WHERE raw_key = ANY (%s)", (unseen,))
    conn.commit()
    client = console(settings, StubModel(wrong={"lift": "Electrical"}, low_confidence={"a/c"}))
    first = client.post("/runs").json()
    assert first["checks"] == "fail"                                     # over 500 rows are now unmapped: the load completes and says so
    assert {a["signal"] for a in client.get(f"/runs/{first['run_id']}").json()["alerts"]} == {"unmapped_rows_pct"}
    assert client.get("/agents").json()["unmapped"][0] == {"task": "category", "to_send": 3, "held": 2}

    assert client.post("/agents/classify", json={"task": "category"}).status_code == 202
    job = wait_for_job(client)
    assert job["status"] == "finished" and job["summary"]["category"]["sent"] == 3
    queue = client.get("/review").json()
    assert {m["key"]: (m["value"], m["status"]) for m in queue["mappings"]} == {
        "a/c": ("HVAC", "proposed"), "exterminator": ("Pest Control", "provisional"), "lift": ("Electrical", "provisional")}
    assert client.get("/overview").json()["pending"] == {"mappings": 3, "rules": 0}

    # the reviewer corrects the wrong answer while approving, rejects one, and leaves the third alone
    decide = lambda **body: client.post("/review/mappings", json={"task": "category", **body})   # noqa: E731
    assert decide(keys=["lift"], decision="approve", set_value="Lifts").status_code == 400      # not a standard category
    assert decide(keys=["lift"], decision="approve", set_value="Elevator").json() == {"changed": 1, "by": settings.approver}
    assert decide(keys=["a/c"], decision="reject").json()["changed"] == 1
    assert decide(keys=["no such key"], decision="approve").json()["changed"] == 0
    assert dict(read(conn, "SELECT raw_key, status FROM silver.category_map WHERE source <> 'seed'")) == {
        "lift": "approved", "a/c": "rejected", "exterminator": "provisional"}
    assert read(conn, "SELECT category_std, source, approved_by FROM silver.category_map WHERE raw_key = 'lift'") == [
        ("Elevator", "human", settings.approver)]

    # an approval is a new rule set; silver changes on the next run, not before
    overview = client.get("/overview").json()
    assert overview["rule_set_version"] != overview["built_with"]
    client.post("/runs")
    overview = client.get("/overview").json()
    assert overview["rule_set_version"] == overview["built_with"]
    assert read(conn, "SELECT DISTINCT category_std FROM silver.tickets WHERE lower(category_raw) = 'lift'") == [("Elevator",)]


def test_rule_proposals_show_their_validation_and_only_validated_ones_can_be_approved(clean, settings):
    client = console(settings, StubModel(dq_script=DQ_SCRIPT))
    assert client.post("/agents/dq", json={}).status_code == 202
    assert client.post("/agents/classify", json={}).status_code in (202, 409)       # 409 while the first job is running
    job = wait_for_job(client)
    assert job["status"] == "finished" and job["summary"]["dq"]["proposals"] == 6

    proposals = {r["rule_id"]: r for r in client.get("/review").json()["rules"]}
    assert {name: (p["validation"], p["expected_rows"], p["measured_rows"]) for name, p in proposals.items()} == {
        "invalid_ticket_id": ("validated", 26, 26), "cost_sentinel": ("validated", 192, 192),
        "cost_currency": ("rejected_count_mismatch", 5000, 993), "cost_evil": ("rejected_unsafe_sql", 1, None),
        "cost_broken": ("rejected_sql_error", 1, None), "rekeyed_duplicates": ("validated", 398, 398)}
    decide = lambda names, decision: client.post("/review/rules", json={                       # noqa: E731
        "ids": [proposals[name]["id"] for name in names], "decision": decision}).json()["changed"]
    assert decide(["cost_sentinel", "cost_evil", "cost_currency"], "approve") == 1             # only the validated one
    assert decide(["cost_evil", "cost_currency"], "reject") == 2
    assert len(client.get("/review").json()["rules"]) == 3

    # the approved predicate now runs as a check on every load and is reported beside the documented rules
    assert client.post("/runs").json()["agent_checks"] == 1
    check = [r for r in client.get("/quality").json()["rules"] if r["origin"] == "agent"]
    assert [(r["id"], r["rows_affected"]) for r in check] == [("A:cost_sentinel", 192)]
    # calls made by the stub are not usage: the console shows none, exactly as the report does
    assert client.get("/agents").json()["calls"] == []


def test_agents_cannot_be_started_without_a_provider(clean, settings):
    client = console(settings)                       # the test settings have LLM_PROVIDER=none and there is no stub
    status = client.get("/agents").json()
    assert status["enabled"] is False and status["provider"] == "none" and status["job"] == {"status": "idle"}
    assert client.post("/agents/dq", json={}).status_code == 409
    assert client.post("/agents/other", json={}).status_code == 422
