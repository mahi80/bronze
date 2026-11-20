"""The approved rule set: seed mappings kept in the repository, and the version stamp silver records."""
import csv
import hashlib
import logging
from pathlib import Path

import psycopg

from .config import ROOT

log = logging.getLogger("pipeline")

# file name -> (table, value columns). Every seed file starts with the raw_key column.
SEED_FILES = {
    "category_map.csv": ("silver.category_map", ["category_std"]),
    "priority_map.csv": ("silver.priority_map", ["priority_std"]),
    "person_map.csv": ("silver.person_map", ["person_id", "person_name"]),
    "issue_map.csv": ("silver.issue_map", ["issue_type", "hazard_signal"]),
}
APPLIED = ("approved", "provisional")


def load_seeds(conn: psycopg.Connection, rules_dir: Path) -> dict:
    """Insert seed mappings that are not in the database yet. Decisions already in the database win."""
    inserted = {}
    for file_name, (table, value_columns) in SEED_FILES.items():
        path = rules_dir / file_name
        if not path.exists():
            continue
        with path.open(newline="", encoding="utf-8") as handle:
            rows = list(csv.DictReader(handle))
        columns = ["raw_key", *value_columns]
        placeholders = ", ".join(["%s"] * len(columns))
        count = 0
        with conn.cursor() as cur:
            for row in rows:
                cur.execute(
                    f"INSERT INTO {table} ({', '.join(columns)}, status, source, approved_by, approved_at) "
                    f"VALUES ({placeholders}, 'approved', 'seed', 'seed', now()) ON CONFLICT (raw_key) DO NOTHING",
                    [row[c] for c in columns],
                )
                count += cur.rowcount
        inserted[table] = count
    conn.commit()
    if any(inserted.values()):
        log.info("seed mappings inserted: %s", inserted)
    return inserted


def export_approved(conn: psycopg.Connection, rules_dir: Path) -> dict:
    """Write every approved mapping back to the seed files, so an approval becomes a reviewable commit."""
    written = {}
    for file_name, (table, value_columns) in SEED_FILES.items():
        columns = ["raw_key", *value_columns]
        rows = conn.execute(f"SELECT {', '.join(columns)} FROM {table} WHERE status = 'approved' ORDER BY {value_columns[0]}, raw_key").fetchall()
        if not rows and not (rules_dir / file_name).exists():
            continue
        with (rules_dir / file_name).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.writer(handle)
            writer.writerow(columns)
            writer.writerows(rows)
        written[file_name] = len(rows)
    return written


def rule_set_version(conn: psycopg.Connection) -> str:
    """A short hash of everything that decides what silver looks like: applied mappings and the silver SQL."""
    digest = hashlib.sha256()
    digest.update((ROOT / "sql" / "20_silver.sql").read_bytes())
    for table, value_columns in SEED_FILES.values():
        for row in conn.execute(f"SELECT raw_key, {', '.join(value_columns)}, status FROM {table} "
                                "WHERE status IN ('approved', 'provisional') ORDER BY raw_key"):
            digest.update(repr(row).encode("utf-8"))
    return digest.hexdigest()[:12]
