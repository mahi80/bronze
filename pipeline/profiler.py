"""A statistical profile of bronze. This, never the rows, is what the Data Quality Agent reads.

Per column: blank share, distinct count, most frequent values, value shapes (digits -> 9, letters -> a).
Across columns, three generic scans, because column statistics alone cannot show these problems:
  * near-duplicate rows: identical on all but two non-key columns
  * vocabulary overlap: values of one column that belong to another column's vocabulary (swapped columns)
  * timestamp order: pairs of timestamp columns where the later one precedes the earlier one
The profile has a fixed size per column, so it costs the same at ten thousand rows or ten million.
"""
import itertools
import json
from pathlib import Path

import psycopg

from .bronze import EXPECTED_COLUMNS

TABLE = "bronze.tickets_raw"
TOP_VALUES = 12
TOP_SHAPES = 10
SHAPE = "regexp_replace(regexp_replace({c}, '[A-Za-z]', 'a', 'g'), '[0-9]', '9', 'g')"

# Rule-based tags produced at landing (semantic type, sensitivity). Personal-data columns are profiled by
# shape and count only: their values are never placed in a prompt by the profiler.
PERSON_NAME = r"^([A-Z]\.|[A-Z][a-z]+) [A-Z][a-z]+$"   # "Lisa Chen" or "L. Chen"; not "maintenance team"


def _column_profile(conn: psycopg.Connection, column: str, total: int, mask_values: bool) -> dict:
    c = f'"{column}"'
    blank, distinct, min_len, max_len = conn.execute(
        f"SELECT count(*) FILTER (WHERE btrim({c}) = ''), count(DISTINCT {c}), min(length({c})), max(length({c})) FROM {TABLE}").fetchone()
    shapes = conn.execute(
        f"SELECT {SHAPE.format(c=c)} AS shape, count(*), min({c}) FROM {TABLE} WHERE btrim({c}) <> '' "
        f"GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT {TOP_SHAPES}").fetchall()
    shape_count = conn.execute(f"SELECT count(DISTINCT {SHAPE.format(c=c)}) FROM {TABLE} WHERE btrim({c}) <> ''").fetchone()[0]
    values = conn.execute(
        f"SELECT {c}, count(*) FROM {TABLE} WHERE btrim({c}) <> '' GROUP BY 1 ORDER BY 2 DESC, 1 LIMIT {TOP_VALUES}").fetchall()
    numeric = conn.execute(
        f"SELECT count(*), min(v), max(v) FROM (SELECT replace({c}, '$', '')::numeric AS v FROM {TABLE} "
        f"WHERE replace({c}, '$', '') ~ '^-?[0-9]+([.][0-9]+)?$' AND length({c}) < 15) x").fetchone()
    padded = conn.execute(f"SELECT count(*) FROM {TABLE} WHERE {c} <> btrim({c})").fetchone()[0]
    profile = {
        "column": column, "rows": total, "blank": blank, "blank_pct": round(100.0 * blank / total, 2),
        "distinct": distinct, "min_length": min_len, "max_length": max_len, "padded_whitespace": padded,
        "distinct_shapes": shape_count,
        "shapes": [{"shape": s[:60], "rows": n, **({} if mask_values else {"example": e[:60]})} for s, n, e in shapes],
        "top_values": [] if mask_values else [{"value": v[:80], "rows": n} for v, n in values],
    }
    if numeric[0]:
        profile["numeric"] = {"rows": numeric[0], "min": float(numeric[1]), "max": float(numeric[2])}
    if mask_values:
        profile["note"] = "values withheld: column tagged as personal data"
    return profile


def tag_columns(conn: psycopg.Connection) -> dict:
    """Metadata tagging at landing: semantic type and sensitivity per column, from value shapes.
    Called by the bronze stage when a file lands, and again whenever a profile is built. The caller commits."""
    total = conn.execute(f"SELECT count(*) FROM {TABLE}").fetchone()[0] or 1
    tags = {}
    for column in EXPECTED_COLUMNS:
        c = f'"{column}"'
        filled, distinct, ts, money, name, ident, avg_len = conn.execute(
            f"SELECT count(*) FILTER (WHERE btrim({c}) <> ''), count(DISTINCT {c}), "
            f"count(*) FILTER (WHERE silver.ts_format(btrim({c})) IS NOT NULL), "
            f"count(*) FILTER (WHERE {c} ~ '^[$]?[0-9]+[.][0-9]{{1,2}}$'), "
            f"count(*) FILTER (WHERE {c} ~ %s), "
            f"count(*) FILTER (WHERE {c} ~ '^[A-Z]+-[0-9]+$'), avg(length({c})) FROM {TABLE}", (PERSON_NAME,)).fetchone()
        filled = filled or 1
        if ident / filled > 0.9 and distinct / filled > 0.9:
            tag = ("identifier", "none", "fixed pattern, unique")
        elif ts / filled > 0.9:
            tag = ("timestamp", "none", "registered timestamp shapes")
        elif money / filled > 0.5:
            tag = ("money", "commercially_sensitive", "currency symbol or two decimals")
        elif name / filled > 0.6 and distinct < 1000:
            tag = ("person_name", "personal_data", "name shapes: two words, or initial and surname")
        elif float(avg_len or 0) > 30 or distinct / total > 0.05:
            tag = ("free_text", "may_contain_personal_data", "long, high-cardinality text written by people")
        elif distinct <= 200:
            tag = ("enumeration", "none", "low cardinality")
        else:
            tag = ("text", "none", "no rule matched")
        tags[column] = {"semantic_type": tag[0], "sensitivity": tag[1], "basis": tag[2]}
        conn.execute(
            "INSERT INTO ops.column_tags (source, column_name, semantic_type, sensitivity, basis) VALUES ('tickets', %s, %s, %s, %s) "
            "ON CONFLICT (source, column_name) DO UPDATE SET semantic_type = excluded.semantic_type, "
            "sensitivity = excluded.sensitivity, basis = excluded.basis, tagged_at = now()", (column, *tag))
    return tags


def _near_duplicates(conn: psycopg.Connection, key: str) -> list[dict]:
    """Rows identical on every column except the key and at most two others."""
    others = [c for c in EXPECTED_COLUMNS if c != key]
    findings = []
    for left_out in [()] + [(c,) for c in others] + list(itertools.combinations(others, 2)):
        group_by = ", ".join(f'"{c}"' for c in others if c not in left_out)
        groups, rows = conn.execute(
            f"SELECT count(*), coalesce(sum(n), 0) FROM (SELECT count(*) AS n FROM {TABLE} "
            f"WHERE \"{key}\" ~ '^[A-Za-z]+-[0-9]+$' GROUP BY {group_by} HAVING count(*) > 1) g").fetchone()
        if groups:
            findings.append({"identical_except": [key, *left_out], "groups": groups, "rows": int(rows)})
    # keep the informative ones: drop a finding when a stricter one (fewer excluded columns) explains all its groups
    findings.sort(key=lambda f: (len(f["identical_except"]), -f["rows"]))
    kept = []
    for f in findings:
        covered = max((k["groups"] for k in kept if set(k["identical_except"]) < set(f["identical_except"])), default=0)
        if f["groups"] > covered:
            kept.append(f)
    return sorted(kept, key=lambda f: -f["rows"])[:5]


def _vocabulary_overlap(conn: psycopg.Connection, tags: dict) -> list[dict]:
    """Values of column A that belong to the frequent vocabulary of enumeration column B."""
    enumerations = [c for c, t in tags.items() if t["semantic_type"] == "enumeration"]
    findings = []
    for b in enumerations:
        for a in EXPECTED_COLUMNS:
            if a == b:
                continue
            rows, sample = conn.execute(
                f"WITH vocab AS (SELECT \"{b}\" AS v FROM {TABLE} WHERE \"{b}\" ~ '[A-Za-z]{{2}}' "
                f"AND lower(btrim(\"{b}\")) NOT IN ('n/a', 'null', 'none', 'unknown', 'tbd') GROUP BY 1 HAVING count(*) >= 20) "
                f"SELECT count(*), min(t.\"{a}\") FROM {TABLE} t JOIN vocab ON vocab.v = t.\"{a}\" "
                f"WHERE NOT EXISTS (SELECT 1 FROM vocab v2 WHERE v2.v = t.\"{b}\")").fetchone()
            if rows:
                findings.append({"column": a, "holds_values_of": b, "rows": rows, "example": (sample or "")[:60],
                                 "meaning": f"{rows} rows have a {b}-style value in {a} while {b} itself holds something else"})
    return sorted(findings, key=lambda f: -f["rows"])[:6]


def _timestamp_order(conn: psycopg.Connection, tags: dict) -> list[dict]:
    stamps = [c for c, t in tags.items() if t["semantic_type"] == "timestamp"]
    findings = []
    for first, second in itertools.combinations(stamps, 2):
        both, reversed_rows = conn.execute(
            f"SELECT count(*), count(*) FILTER (WHERE b < a) FROM (SELECT silver.parse_ts(btrim(\"{first}\")) AS a, "
            f"silver.parse_ts(btrim(\"{second}\")) AS b FROM {TABLE}) x WHERE a IS NOT NULL AND b IS NOT NULL").fetchone()
        if both:
            findings.append({"earlier_column": first, "later_column": second, "rows_with_both": both,
                             "rows_where_later_precedes_earlier": reversed_rows,
                             "pct": round(100.0 * reversed_rows / both, 1)})
    return findings


def build(conn: psycopg.Connection) -> dict:
    total = conn.execute(f"SELECT count(*) FROM {TABLE}").fetchone()[0]
    tags = tag_columns(conn)
    conn.commit()
    columns = [_column_profile(conn, c, total, mask_values=tags[c]["sensitivity"] == "personal_data") for c in EXPECTED_COLUMNS]
    for profile in columns:
        profile["tags"] = tags[profile["column"]]
    key = next((c for c, t in tags.items() if t["semantic_type"] == "identifier"), EXPECTED_COLUMNS[0])
    return {
        "table": TABLE, "rows": total, "purpose": "Operational support tickets for building facilities: one row per ticket.",
        "columns": columns,
        "cross_column": {
            "candidate_key": key,
            "near_duplicates": _near_duplicates(conn, key),
            "vocabulary_overlap": _vocabulary_overlap(conn, tags),
            "timestamp_order": _timestamp_order(conn, tags),
        },
    }


def write(profile: dict, reports_dir: Path) -> Path:
    reports_dir.mkdir(parents=True, exist_ok=True)
    path = reports_dir / "profile.json"
    path.write_text(json.dumps(profile, indent=1, ensure_ascii=False), encoding="utf-8")
    return path
