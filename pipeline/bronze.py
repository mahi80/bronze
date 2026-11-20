"""Bronze: load each delivered file exactly as it is, with lineage metadata. Re-runnable."""
import csv
import hashlib
import logging
from collections import Counter
from pathlib import Path

import psycopg
from psycopg.types.json import Jsonb

from . import db

log = logging.getLogger("pipeline")

SOURCE = "tickets"
EXPECTED_COLUMNS = ["ticket_id", "created_at", "resolved_at", "category", "priority", "status", "building",
                    "description", "submitted_by", "assigned_to", "resolution_notes", "cost", "sla_hours"]
FIELD_SEPARATOR = "\x1f"  # ASCII unit separator: cannot appear in a CSV field we accept


class SchemaDrift(Exception):
    """The file's header does not match the registered schema. Nothing is loaded until a person decides."""


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def row_hash(fields: list[str]) -> str:
    return hashlib.sha256(FIELD_SEPARATOR.join(fields).encode("utf-8")).hexdigest()


def _check_header(conn: psycopg.Connection, run_id: int, path: Path, header: list[str]) -> None:
    registered = conn.execute("SELECT columns FROM ops.schema_registry WHERE source = %s", (SOURCE,)).fetchone()
    expected = registered[0] if registered else EXPECTED_COLUMNS
    if header != expected:
        detail = {"expected": expected, "found": header,
                  "added": [c for c in header if c not in expected], "missing": [c for c in expected if c not in header]}
        conn.execute("INSERT INTO ops.drift_event (run_id, source_file, kind, detail) VALUES (%s, %s, 'header_changed', %s)",
                     (run_id, path.name, Jsonb(detail)))
        conn.commit()  # keep the evidence even though the stage fails
        raise SchemaDrift(f"{path.name}: header changed; added={detail['added']} missing={detail['missing']}. "
                          "File not loaded. Review the change, then update ops.schema_registry.")
    if not registered:
        conn.execute("INSERT INTO ops.schema_registry (source, columns) VALUES (%s, %s)", (SOURCE, header))


def load_file(conn: psycopg.Connection, run_id: int, path: Path) -> dict:
    """Load one file. Returns counts. A file whose hash is already in the manifest is skipped."""
    sha = file_sha256(path)
    if conn.execute("SELECT 1 FROM ops.file_manifest WHERE file_sha256 = %s", (sha,)).fetchone():
        log.info("run=%s file=%s already loaded (sha256 %s...), skipping", run_id, path.name, sha[:12])
        return {"file": path.name, "read": 0, "inserted": 0, "skipped_file": True}

    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        header = next(reader)
        _check_header(conn, run_id, path, header)
        seen: Counter = Counter()
        rows = []
        for number, fields in enumerate(reader, start=1):
            if len(fields) != len(header):
                raise ValueError(f"{path.name} row {number}: {len(fields)} fields, expected {len(header)}")
            if any(FIELD_SEPARATOR in field for field in fields):
                raise ValueError(f"{path.name} row {number}: contains the reserved separator character")
            digest = row_hash(fields)
            seen[digest] += 1
            rows.append((*fields, path.name, sha, number, digest, seen[digest], run_id))

    with conn.cursor() as cur:
        cur.execute("CREATE TEMP TABLE _landing (LIKE bronze.tickets_raw INCLUDING DEFAULTS) ON COMMIT DROP")
        columns = ", ".join(EXPECTED_COLUMNS + ["_source_file", "_file_sha256", "_source_row_number", "_row_hash", "_dup_seq", "_batch_id"])
        with cur.copy(f"COPY _landing ({columns}) FROM STDIN") as copy:
            for row in rows:
                copy.write_row(row)
        cur.execute(f"INSERT INTO bronze.tickets_raw ({columns}) SELECT {columns} FROM _landing "
                    "ON CONFLICT (_row_hash, _dup_seq) DO NOTHING")
        inserted = cur.rowcount
        cur.execute("DROP TABLE _landing")
        cur.execute("INSERT INTO ops.file_manifest (file_sha256, source_file, row_count, batch_id) VALUES (%s, %s, %s, %s)",
                    (sha, path.name, len(rows), run_id))
    db.lineage(conn, run_id, f"file:{path.name}", "bronze.tickets_raw", "bronze.load_file (as-is copy)")
    return {"file": path.name, "read": len(rows), "inserted": inserted, "skipped_file": False}


def run(conn: psycopg.Connection, run_id: int, data_dir: Path) -> dict:
    files = sorted(data_dir.glob("*.csv"))
    if not files:
        raise FileNotFoundError(f"no .csv files in {data_dir}")
    with db.stage(conn, run_id, "bronze") as counts:
        results = [load_file(conn, run_id, path) for path in files]
        total = conn.execute("SELECT count(*) FROM bronze.tickets_raw").fetchone()[0]
        read = sum(r["read"] for r in results)
        inserted = sum(r["inserted"] for r in results)
        tagged = 0
        if any(not r["skipped_file"] for r in results):
            from .profiler import tag_columns      # imported here because profiler imports this module
            tagged = len(tag_columns(conn))         # metadata tags at landing: semantic type and sensitivity
        counts.update(rows_in=read, rows_out=inserted,
                      message=f"files={len(files)} skipped_files={sum(r['skipped_file'] for r in results)} "
                              f"already_present_rows={read - inserted} bronze_total={total} tagged_columns={tagged}")
    return {"files": results, "bronze_total": total}
