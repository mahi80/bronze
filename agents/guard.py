"""Guardrails for SQL written by a model.

Three independent layers:
  1. a text check: one statement, no comments, no writes, no dangerous functions
  2. execution on a separate connection, in a read-only transaction, as role agent_ro (SELECT on bronze only)
  3. a statement timeout and a row limit
A proposal's claimed row count is then compared with the measured one.
"""
import re

import psycopg

from pipeline.config import Settings

TABLE = "bronze.tickets_raw"
WRITE_OR_CONTROL = re.compile(
    r"\b(insert|update|delete|merge|drop|alter|create|truncate|grant|revoke|copy|call|do|execute|vacuum|analyze|refresh|"
    r"listen|notify|set|reset|begin|commit|rollback|savepoint|lock|prepare|deallocate|declare|fetch|cluster|reindex|"
    r"load|discard|into)\b", re.I)
DANGEROUS_FUNCTION = re.compile(
    r"\b(pg_sleep\w*|pg_read_\w+|pg_ls_\w+|pg_stat_file|lo_\w+|dblink\w*|set_config|current_setting|pg_terminate_backend|"
    r"pg_cancel_backend|nextval|setval|pg_advisory\w*|query_to_xml\w*|pg_reload_conf|txid_current|pg_catalog|information_schema)\b", re.I)
STRING_LITERAL = re.compile(r"'(?:[^']|'')*'")
QUOTED_IDENTIFIER = re.compile(r'"(?:[^"]|"")*"')


class UnsafeSQL(ValueError):
    pass


def check_sql(sql: str, kind: str) -> str:
    """Raise UnsafeSQL unless `sql` is a single read-only expression (row_predicate) or SELECT (count_query, probe)."""
    sql = sql.strip().rstrip(";").strip()
    if not sql:
        raise UnsafeSQL("empty SQL")
    if len(sql) > 2000:
        raise UnsafeSQL("SQL longer than 2000 characters")
    if "$$" in sql or re.search(r"\$\w*\$", sql):
        raise UnsafeSQL("dollar-quoted strings are not allowed")
    bare = QUOTED_IDENTIFIER.sub('""', STRING_LITERAL.sub("''", sql))     # ignore words inside literals
    if "'" in bare.replace("''", ""):
        raise UnsafeSQL("unbalanced quote")
    if ";" in bare:
        raise UnsafeSQL("more than one statement")
    if "--" in bare or "/*" in bare:
        raise UnsafeSQL("comments are not allowed")
    match = WRITE_OR_CONTROL.search(bare) or DANGEROUS_FUNCTION.search(bare)
    if match:
        raise UnsafeSQL(f"forbidden keyword or function: {match.group(0)}")
    starts_with_select = re.match(r"^\s*(select|with)\b", bare, re.I) is not None
    if kind == "row_predicate" and starts_with_select:
        raise UnsafeSQL("a row predicate must be a boolean expression, not a query")
    if kind in ("count_query", "probe") and not starts_with_select:
        raise UnsafeSQL("a query must start with SELECT or WITH")
    return sql


class ReadOnlyRunner:
    """Runs checked SQL on its own connection, read-only, as agent_ro, with a timeout."""

    def __init__(self, settings: Settings, timeout_ms: int = 5000):
        self.conn = psycopg.connect(settings.database_url)
        self.timeout_ms = timeout_ms

    def close(self) -> None:
        self.conn.close()

    def _run(self, statement: str) -> list[tuple]:
        try:
            with self.conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                cur.execute("SET LOCAL ROLE agent_ro")
                cur.execute(f"SET LOCAL statement_timeout = {int(self.timeout_ms)}")
                cur.execute(statement)
                return cur.fetchall()
        finally:
            self.conn.rollback()

    def count(self, sql: str, kind: str) -> int:
        """Rows affected by a proposal: rows matching a predicate, or the single integer a count query returns."""
        sql = check_sql(sql, kind)
        if kind == "row_predicate":
            rows = self._run(f"SELECT count(*) FROM {TABLE} WHERE ({sql})")
        else:
            rows = self._run(f"SELECT * FROM ({sql}) q LIMIT 2")
            if len(rows) != 1 or len(rows[0]) != 1 or not isinstance(rows[0][0], (int,)) and not str(rows[0][0]).lstrip("-").isdigit():
                raise UnsafeSQL("a count query must return exactly one integer")
        return int(rows[0][0])

    def matching_rows(self, sql: str) -> set[str]:
        """Row hashes matching a row predicate. Used by the evaluation harness to compare with known issues."""
        sql = check_sql(sql, "row_predicate")
        return {row[0] for row in self._run(f"SELECT _row_hash FROM {TABLE} WHERE ({sql})")}

    def probe(self, sql: str, limit: int = 20) -> list[dict]:
        """Run a read-only exploration query and return at most `limit` rows."""
        sql = check_sql(sql, "probe")
        try:
            with self.conn.cursor() as cur:
                cur.execute("SET TRANSACTION READ ONLY")
                cur.execute("SET LOCAL ROLE agent_ro")
                cur.execute(f"SET LOCAL statement_timeout = {int(self.timeout_ms)}")
                cur.execute(f"SELECT * FROM ({sql}) q LIMIT {int(limit)}")
                names = [d.name for d in cur.description]
                return [dict(zip(names, (str(v)[:120] if v is not None else None for v in row))) for row in cur.fetchall()]
        finally:
            self.conn.rollback()
