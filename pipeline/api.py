"""HTTP API for the optional operator console in console/. Start it with: uvicorn pipeline.api:app

It adds no pipeline logic. Read routes query the ops, silver and gold tables; write routes call the same
functions as the command line (run, approve, reject, agents). Every route except /health needs the bearer
token from CONSOLE_API_TOKEN, which the console keeps on its server side and never sends to a browser.
"""
import hashlib
import hmac
import re
import threading
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

import psycopg
from fastapi import APIRouter, Depends, FastAPI, File, HTTPException, Query, Request, UploadFile
from fastapi.responses import JSONResponse
from psycopg.rows import dict_row
from pydantic import BaseModel, Field

from agents import classifier, review
from agents import cli as agents_cli
from agents.llm import LLM, LLMDisabled, estimate_cost

from . import bronze, checks, cli, config, db, gold, profiler, rules, silver

RUN_COLUMNS = ("SELECT run_id, command, status, started_at, finished_at, rule_set_version, message, "
               "round(extract(epoch FROM finished_at - started_at) * 1000) AS duration_ms FROM ops.pipeline_run")
GOLD_MODELS = {name.split(".")[1]: name for name in gold.MODELS}
NOT_RUN = "The pipeline has not run in this database yet. Run it once to create the tables."
MAX_UPLOAD_BYTES = 20 * 1024 * 1024      # a delivered file of this size is already far above any realistic batch


class MappingDecision(BaseModel):
    task: Literal["category", "priority", "person", "issue"]
    keys: list[str] = Field(min_length=1, max_length=500)      # exactly the proposals the reviewer saw
    decision: Literal["approve", "reject"]
    set_value: str | None = Field(None, max_length=80)         # a correction: approve as this standard category


class RuleDecision(BaseModel):
    ids: list[int] = Field(min_length=1, max_length=200)
    decision: Literal["approve", "reject"]


class AgentRequest(BaseModel):
    task: Literal["all", "category", "person", "issue"] = "all"


def rows(conn: psycopg.Connection, sql: str, params: tuple = ()) -> list[dict]:
    with conn.cursor(row_factory=dict_row) as cur:
        return cur.execute(sql, params).fetchall()


def create_app(settings: config.Settings | None = None, transport=None) -> FastAPI:
    """`transport` replaces the model provider; the tests pass a stub, as the agent tests do."""
    settings = settings or config.load()
    app = FastAPI(title="Medallion pipeline console API")
    api = APIRouter()
    job: dict = {"status": "idle"}          # one agent job at a time, kept in memory: this is a single-process local tool
    job_lock = threading.Lock()

    def authorised(request: Request) -> None:
        if not settings.console_api_token:
            raise HTTPException(503, "CONSOLE_API_TOKEN is not set for the API")
        supplied = request.headers.get("authorization", "")
        if not hmac.compare_digest(supplied.encode(), f"Bearer {settings.console_api_token}".encode()):
            raise HTTPException(401, "missing or wrong bearer token")

    def connection():
        try:
            conn = db.connect(settings, retries=3)
        except RuntimeError as exc:
            raise HTTPException(503, str(exc))
        try:
            yield conn
        finally:
            conn.close()

    @app.exception_handler(psycopg.errors.UndefinedTable)
    @app.exception_handler(psycopg.errors.InvalidSchemaName)
    async def not_run_yet(request: Request, exc: Exception) -> JSONResponse:
        return JSONResponse({"detail": NOT_RUN}, status_code=409)

    @app.get("/health")
    def health():
        return {"status": "ok"}

    # ------------------------------------------------------------------ files
    @api.post("/files")
    async def upload_file(file: UploadFile = File(...)):
        """Accept a delivered CSV into the uploads directory. The next run loads it through the same bronze path
        as data/, so the manifest, header check and row hashes apply unchanged."""
        name = Path(file.filename or "").name
        if not name.lower().endswith(".csv"):
            raise HTTPException(400, "Only .csv files are accepted.")
        content = await file.read(MAX_UPLOAD_BYTES + 1)
        if len(content) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "The file is larger than 20 MB.")
        if not content.strip():
            raise HTTPException(400, "The file is empty.")
        digest = hashlib.sha256(content).hexdigest()
        safe = re.sub(r"[^A-Za-z0-9._-]+", "-", name)[:80]
        settings.uploads_dir.mkdir(parents=True, exist_ok=True)
        path = settings.uploads_dir / f"{digest[:12]}-{safe}"
        path.write_bytes(content)                 # same content lands on the same name: the manifest skips a re-delivery
        return {"file": path.name, "bytes": len(content), "sha256": digest[:12]}

    # ------------------------------------------------------------------ runs
    @api.get("/overview")
    def overview(conn=Depends(connection)):
        try:
            # the row accounting the silver stage asserted, not a recount: bronze = silver + rejected + superseded
            accounting = rows(conn, "SELECT run_id, rows_in AS bronze, rows_out AS silver, rows_rejected AS rejected, "
                                    "rows_in - rows_out - rows_rejected AS superseded FROM ops.stage_log "
                                    "WHERE stage = 'silver' AND status = 'succeeded' ORDER BY finished_at DESC LIMIT 1")
        except (psycopg.errors.UndefinedTable, psycopg.errors.InvalidSchemaName):
            return {"initialised": False, "provider": settings.llm_provider, "approver": settings.approver}
        if accounting:
            accounting[0]["duplicates"] = conn.execute("SELECT count(*) FROM silver.tickets WHERE is_duplicate").fetchone()[0]
        last_run = rows(conn, RUN_COLUMNS + " ORDER BY run_id DESC LIMIT 1")
        built = conn.execute("SELECT rule_set_version FROM ops.pipeline_run WHERE status = 'succeeded' ORDER BY run_id DESC LIMIT 1").fetchone()
        pending = review.pending(conn)
        return {
            "initialised": True,
            "accounting": accounting[0] if accounting else None,
            "last_run": last_run[0] if last_run else None,
            "rule_set_version": rules.rule_set_version(conn),          # what the next run would build with
            "built_with": built[0] if built else None,                 # what silver and gold were built with
            "signals": checks.evaluate(conn, settings),
            "gold": [{"model": name, "rows": conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]} for name, table in GOLD_MODELS.items()],
            "pending": {"mappings": sum(p["task"] != "rule" for p in pending), "rules": sum(p["task"] == "rule" for p in pending)},
            "files": rows(conn, "SELECT source_file, row_count, left(file_sha256, 12) AS sha256, loaded_at FROM ops.file_manifest ORDER BY loaded_at"),
            "provider": settings.llm_provider,
            "approver": settings.approver,
        }

    @api.get("/runs")
    def list_runs(limit: int = Query(50, ge=1, le=200), conn=Depends(connection)):
        return {"runs": rows(conn, RUN_COLUMNS + " ORDER BY run_id DESC LIMIT %s", (limit,)),
                "total": conn.execute("SELECT count(*) FROM ops.pipeline_run").fetchone()[0]}

    @api.post("/runs")
    def start_run(conn=Depends(connection)):
        try:
            return {"status": "succeeded", **cli.run_once(conn, settings)}
        except cli.RunInProgress as exc:
            raise HTTPException(409, str(exc))
        except cli.RunFailed as exc:
            return {"status": "failed", "run_id": exc.run_id, "message": str(exc)}

    @api.get("/runs/{run_id}")
    def run_detail(run_id: int, conn=Depends(connection)):
        run = rows(conn, RUN_COLUMNS + " WHERE run_id = %s", (run_id,))
        if not run:
            raise HTTPException(404, f"run {run_id} not found")
        return {
            "run": run[0],
            "stages": rows(conn, "SELECT stage, status, rows_in, rows_out, rows_rejected, message, "
                                 "round(extract(epoch FROM finished_at - started_at) * 1000) AS duration_ms "
                                 "FROM ops.stage_log WHERE run_id = %s ORDER BY started_at", (run_id,)),
            "rules": rows(conn, "SELECT rule_id, title, rows_checked, rows_affected, pct FROM ops.dq_results "
                                "WHERE run_id = %s ORDER BY rule_id LIKE 'A:%%', rule_id", (run_id,)),
            "alerts": rows(conn, "SELECT signal, level, value, threshold, message FROM ops.alert WHERE run_id = %s ORDER BY id", (run_id,)),
            "drift": rows(conn, "SELECT kind, source_file, detail, detected_at FROM ops.drift_event WHERE run_id = %s ORDER BY id", (run_id,)),
            "lineage": rows(conn, "SELECT source_object, target_object, transformation, rule_set_version, executed_by, executed_at "
                                  "FROM ops.lineage_edge WHERE run_id = %s ORDER BY executed_at", (run_id,)),
        }

    # ------------------------------------------------------------------ data
    @api.get("/quality")
    def quality(conn=Depends(connection)):
        """Every documented rule with its measured effect in the latest run, and what was quarantined or flagged."""
        run_id = conn.execute("SELECT max(run_id) FROM ops.dq_results").fetchone()[0]
        measured = {r["rule_id"]: r for r in rows(conn, "SELECT rule_id, title, rows_checked, rows_affected, pct FROM ops.dq_results WHERE run_id = %s", (run_id,))}
        blank = {"rows_checked": None, "rows_affected": None, "pct": None}
        documented = [{"id": r["id"], "title": r["title"], "rule": r["rule"], "why": r["why"], "scope": r["scope"], "origin": "documented",
                       **{k: measured.get(r["id"], blank)[k] for k in blank}} for r in silver.load_cleaning_rules(settings)]
        from_agents = [{"id": rule_id, "title": m["title"], "rule": "An approved Data Quality Agent proposal, run as a check on every load.",
                        "why": "", "scope": "bronze", "origin": "agent", **{k: m[k] for k in blank}}
                       for rule_id, m in sorted(measured.items()) if rule_id.startswith("A:")]
        tags = {t["column_name"]: t for t in rows(conn, "SELECT column_name, semantic_type, sensitivity, basis FROM ops.column_tags WHERE source = %s", (bronze.SOURCE,))}
        return {
            "run_id": run_id,
            "rules": documented + from_agents,
            "reject_reasons": rows(conn, "SELECT reason, count(*) AS rows FROM silver.tickets_rejected, unnest(reasons) AS reason GROUP BY 1 ORDER BY 2 DESC, 1"),
            "rejected": rows(conn, "SELECT _source_row_number AS source_row, reasons, raw FROM silver.tickets_rejected ORDER BY 1 LIMIT 200"),
            "duplicates": rows(conn, "SELECT dup_match_strength AS strength, count(*) AS rows FROM silver.tickets WHERE is_duplicate GROUP BY 1 ORDER BY 2 DESC"),
            "flags": rows(conn, "SELECT flag, count(*) AS rows FROM silver.tickets, unnest(dq_flags) AS flag GROUP BY 1 ORDER BY 2 DESC, 1"),
            "tags": [tags[c] for c in bronze.EXPECTED_COLUMNS if c in tags],
        }

    @api.get("/data/profile")
    def data_profile(conn=Depends(connection)):
        """The same fixed-size profile the Data Quality Agent reads, so the console shows what the agent sees."""
        try:
            profile = profiler.build(conn)
        except (psycopg.errors.UndefinedTable, psycopg.errors.InvalidSchemaName):
            return {"initialised": False}
        return {"initialised": True, **profile}

    @api.get("/data/rows")
    def data_rows(limit: int = Query(100, ge=1, le=100), conn=Depends(connection)):
        """The first rows of bronze, as delivered: the landing table, not the cleaned one."""
        columns = ", ".join(f'"{c}"' for c in bronze.EXPECTED_COLUMNS)
        try:
            data = rows(conn, f"SELECT _source_file, _source_row_number AS file_row, {columns} FROM bronze.tickets_raw "
                              "ORDER BY _batch_id, _source_row_number LIMIT %s", (limit,))
            total = conn.execute("SELECT count(*) FROM bronze.tickets_raw").fetchone()[0]
        except (psycopg.errors.UndefinedTable, psycopg.errors.InvalidSchemaName):
            return {"initialised": False, "columns": bronze.EXPECTED_COLUMNS, "rows": [], "total": 0}
        return {"initialised": True, "columns": bronze.EXPECTED_COLUMNS, "rows": data, "total": total}

    @api.get("/gold/{model}")
    def gold_model(model: str, limit: int = Query(2000, ge=1, le=5000), conn=Depends(connection)):
        if model not in GOLD_MODELS:                   # the table name never comes from the request
            raise HTTPException(404, f"unknown gold model; choose one of {sorted(GOLD_MODELS)}")
        table = GOLD_MODELS[model]
        data = rows(conn, f"SELECT * FROM {table} ORDER BY 1, 2, 3 LIMIT %s", (limit,))
        for row in data:
            del row["_built_at"]
        return {"model": model, "rows": data, "total": conn.execute(f"SELECT count(*) FROM {table}").fetchone()[0]}

    @api.get("/tickets/{ticket_id}")
    def ticket_trace(ticket_id: str, conn=Depends(connection)):
        """One ticket through the layers: every bronze row delivered under this ID, the silver row, and its copies."""
        key = ticket_id.strip().upper()[:40]
        delivered = rows(conn, "SELECT b.*, r.reasons AS reject_reasons FROM bronze.tickets_raw b "
                               "LEFT JOIN silver.tickets_rejected r USING (_row_hash, _dup_seq) "
                               "WHERE upper(silver.norm_text(b.ticket_id)) = %s ORDER BY b._batch_id, b._source_row_number", (key,))
        cleaned = rows(conn, "SELECT * FROM silver.tickets WHERE ticket_id = %s", (key,))
        if not delivered and not cleaned:
            raise HTTPException(404, f"no ticket {key} in bronze or silver")
        return {"ticket_id": key, "bronze": delivered, "silver": cleaned[0] if cleaned else None,
                "copies": rows(conn, "SELECT ticket_id, dup_match_strength FROM silver.tickets WHERE duplicate_of = %s ORDER BY ticket_number", (key,))}

    # ------------------------------------------------------------------ the approval gate
    @api.get("/review")
    def review_queue(conn=Depends(connection)):
        pending = review.pending(conn)
        mappings = [p for p in pending if p["task"] != "rule"]
        counts = review.affected_counts(conn, [(p["task"], p["key"]) for p in mappings])
        for proposal in mappings:
            proposal["affected"] = counts.get((proposal["task"], proposal["key"]), 0)
        rule_set = []
        for task, (table, _) in review.MAPS.items():
            counts = rows(conn, f"SELECT count(*) FILTER (WHERE status = 'approved') AS approved, "
                                f"count(*) FILTER (WHERE status = 'approved' AND source <> 'seed') AS approved_after_review, "
                                f"count(*) FILTER (WHERE status = 'provisional') AS provisional, "
                                f"count(*) FILTER (WHERE status = 'proposed') AS proposed, "
                                f"count(*) FILTER (WHERE status = 'rejected') AS rejected FROM {table}")[0]
            rule_set.append({"task": task, **counts})
        rejected = review.rejected_mappings(conn)
        reopened = review.affected_counts(conn, [(r["task"], r["key"]) for r in rejected])
        for item in rejected:
            item["affected"] = reopened.get((item["task"], item["key"]), 0)
        return {
            "approver": settings.approver,
            "mappings": mappings,
            "rejected": rejected,
            "rules": rows(conn, "SELECT id, agent_run, rule_id, column_names, finding, why_it_matters, action, risk, sql_kind, sql, "
                                "expected_rows, measured_rows, validation, validation_msg, model, created_at "
                                "FROM ops.rule_proposal WHERE status = 'proposed' ORDER BY id"),
            "rule_set": rule_set,
            "approved_checks": conn.execute("SELECT count(*) FROM ops.rule_proposal WHERE status = 'approved'").fetchone()[0],
            "rule_set_version": rules.rule_set_version(conn),
            "categories": list(classifier.taxonomy(settings)["categories"]),
        }

    @api.post("/review/mappings")
    def decide_mappings(body: MappingDecision, conn=Depends(connection)):
        if body.decision == "reject":
            changed = review.reject_mappings(conn, body.task, settings.approver, body.keys)
        else:
            if body.set_value is not None and (body.task != "category" or body.set_value not in classifier.taxonomy(settings)["categories"]):
                raise HTTPException(400, "set_value must be one of the standard categories, and applies to category mappings only")
            changed = review.approve_mappings(conn, body.task, settings.approver, body.keys, set_value=body.set_value,
                                              include_rejected=True)
        return {"changed": changed, "by": settings.approver}

    @api.post("/review/rules")
    def decide_rules(body: RuleDecision, conn=Depends(connection)):
        decision = "approved" if body.decision == "approve" else "rejected"
        return {"changed": review.review_rules(conn, body.ids, settings.approver, decision), "by": settings.approver}

    # ------------------------------------------------------------------ agents
    def agents_enabled() -> bool:
        return transport is not None or settings.llm_provider != "none"

    @api.get("/agents")
    def agents(conn=Depends(connection)):
        llm = LLM(settings, None, transport=transport)

        def model(tier: str) -> str | None:
            try:
                return llm.model_for(tier)
            except LLMDisabled:
                return None

        category, held_category = classifier.category_candidates(conn, settings.classify_min_rows)
        person, held_person, _ = classifier.person_candidates(conn, settings.classify_min_rows)
        issue, held_issue = classifier.issue_candidates(conn, settings.classify_min_rows)
        # as in the report: calls made by the test stub or a replay are not usage
        calls = rows(conn, "SELECT task, model, count(*) AS calls, sum(input_tokens) AS tokens_in, sum(output_tokens) AS tokens_out, "
                           "round(avg(latency_ms)) AS mean_latency_ms FROM ops.agent_call "
                           "WHERE provider NOT IN ('stub', 'replay') GROUP BY 1, 2 ORDER BY 1, 2")
        for call in calls:
            call["cost_usd"] = estimate_cost(call["model"], call["tokens_in"], call["tokens_out"])
        return {
            "provider": llm.provider,
            "enabled": agents_enabled(),
            "models": {"classification": model("small"), "rule_proposal": model("strong")},
            "token_budget": settings.llm_token_budget,
            "auto_apply_confidence": settings.auto_apply_confidence,
            "min_rows": settings.classify_min_rows,
            "unmapped": [{"task": "category", "to_send": len(category), "held": len(held_category)},
                         {"task": "person", "to_send": len(person), "held": len(held_person)},
                         {"task": "issue", "to_send": len(issue), "held": len(held_issue)}],
            "calls": calls,
            "job": job,
        }

    @api.get("/agents/job")
    def agent_job():
        return job

    def run_job(agent: str, task: str) -> None:
        try:
            conn = db.connect(settings, retries=3)
            try:
                summary = agents_cli.run_agent(conn, settings, LLM(settings, conn, transport=transport), agent, task)
            finally:
                conn.close()
            job.update(status="finished", summary=summary)
        except Exception as exc:        # a failure in the thread must reach the console, not vanish
            job.update(status="failed", error=f"{type(exc).__name__}: {exc}"[:500])
        job["finished_at"] = datetime.now(timezone.utc)

    @api.post("/agents/{agent}", status_code=202)
    def start_agent(agent: Literal["classify", "dq"], body: AgentRequest):
        """Agent runs take minutes, so they run in the background; the console polls /agents/job."""
        if not agents_enabled():
            raise HTTPException(409, "No model provider is configured. Set LLM_PROVIDER and LLM_API_KEY in .env, then restart the API.")
        with job_lock:
            if job["status"] == "running":
                raise HTTPException(409, f"the {job['agent']} agent is still running")
            job.clear()
            job.update(status="running", agent=agent, task=body.task, started_at=datetime.now(timezone.utc))
        threading.Thread(target=run_job, args=(agent, body.task), daemon=True).start()
        return job

    app.include_router(api, dependencies=[Depends(authorised)])
    return app


app = create_app()
