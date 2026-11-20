"""The human approval gate. Agents write proposals; only these functions make them active.

Mapping statuses: proposed (held) -> approved; provisional (auto-applied by policy) -> approved; any -> rejected.
Approving a mapping changes the rule set version that silver records on its next build.
"""
import psycopg

MAPS = {
    "category": ("silver.category_map", "category_std"),
    "priority": ("silver.priority_map", "priority_std"),
    "person": ("silver.person_map", "person_name"),
    "issue": ("silver.issue_map", "issue_type || ' / ' || hazard_signal"),
}


def pending(conn: psycopg.Connection) -> list[dict]:
    """Everything waiting for a person: held or provisional mappings and unreviewed rule proposals."""
    rows = []
    for task, (table, value) in MAPS.items():
        for raw_key, std, status, confidence, reason, model in conn.execute(
                f"SELECT raw_key, {value}, status, confidence, reason, model FROM {table} "
                "WHERE status IN ('proposed', 'provisional') ORDER BY status, confidence NULLS LAST, raw_key"):
            rows.append({"task": task, "key": raw_key, "value": std, "status": status,
                         "confidence": float(confidence) if confidence is not None else None, "reason": reason, "model": model})
    for rid, rule_id, columns, action, risk, validation, measured, finding in conn.execute(
            "SELECT id, rule_id, column_names, action, risk, validation, measured_rows, finding FROM ops.rule_proposal "
            "WHERE status = 'proposed' ORDER BY id"):
        rows.append({"task": "rule", "key": str(rid), "value": f"{rule_id} [{action}, {risk} risk] on {','.join(columns)}",
                     "status": validation, "confidence": None, "reason": f"{measured} rows: {finding[:120]}", "model": None})
    return rows


def approve_mappings(conn: psycopg.Connection, task: str, approver: str, keys: list[str] | None = None,
                     min_confidence: float | None = None, set_value: str | None = None,
                     include_rejected: bool = False) -> int:
    """Approve held or provisional mappings. `set_value` overrides the proposed value (a correction by the reviewer).
    `include_rejected` lets the console reopen a value that was rejected earlier; the CLI keeps rejected closed."""
    table, _ = MAPS[task]
    where, params = [("status <> 'approved'" if include_rejected else "status IN ('proposed', 'provisional')")], []
    if keys:
        where.append("raw_key = ANY (%s)")
        params.append(keys)
    if min_confidence is not None:
        where.append("confidence >= %s")
        params.append(min_confidence)
    assignments = "status = 'approved', approved_by = %s, approved_at = now()"
    head = [approver]
    if set_value is not None:
        if task in ("person", "issue"):
            raise ValueError("--set is supported for category and priority; edit person and issue mappings in rules/*.csv")
        assignments += f", {MAPS[task][1]} = %s, source = 'human'"
        head.append(set_value)
    count = conn.execute(f"UPDATE {table} SET {assignments} WHERE {' AND '.join(where)}", head + params).rowcount
    conn.commit()
    return count


def reject_mappings(conn: psycopg.Connection, task: str, approver: str, keys: list[str]) -> int:
    table, _ = MAPS[task]
    count = conn.execute(f"UPDATE {table} SET status = 'rejected', approved_by = %s, approved_at = now() "
                         "WHERE raw_key = ANY (%s) AND status <> 'approved'", (approver, keys)).rowcount
    conn.commit()
    return count


def rejected_mappings(conn: psycopg.Connection) -> list[dict]:
    """Values a person rejected. They are not applied, they keep their raw value in silver and stay unmapped;
    the console lists them so a decision can be reopened."""
    out = []
    for task, (table, value) in MAPS.items():
        for raw_key, std, by, at in conn.execute(
                f"SELECT raw_key, {value}, approved_by, approved_at FROM {table} "
                "WHERE status = 'rejected' ORDER BY approved_at DESC NULLS LAST, raw_key LIMIT 200"):
            out.append({"task": task, "key": raw_key, "value": std, "decided_by": by,
                        "decided_at": at.isoformat() if at else None})
    return out


def affected_counts(conn: psycopg.Connection, pairs: list[tuple[str, str]]) -> dict[tuple[str, str], int]:
    """How many valid tickets each unresolved key is in, so a reviewer sees the consequence before deciding."""
    if not pairs:
        return {}
    values = ", ".join(["(%s, %s)"] * len(pairs))
    params = [item for pair in pairs for item in pair]
    result = conn.execute(
        f"""
        WITH keys(task, raw_key) AS (VALUES {values})
        SELECT k.task, k.raw_key, count(t.ticket_id)
        FROM keys k LEFT JOIN silver.tickets t ON
            (k.task = 'category' AND silver.norm_key(t.category_raw) = k.raw_key) OR
            (k.task = 'priority' AND silver.norm_key(t.priority_raw) = k.raw_key) OR
            (k.task = 'person'   AND silver.norm_key(t.submitted_by_raw) = k.raw_key) OR
            (k.task = 'issue'    AND t.description_template = k.raw_key)
        GROUP BY 1, 2
        """, params).fetchall()
    return {(task, key): int(count) for task, key, count in result}


def review_rules(conn: psycopg.Connection, ids: list[int], approver: str, decision: str) -> int:
    """Approve or reject Data Quality Agent proposals. Only validated proposals can be approved."""
    extra = " AND validation = 'validated'" if decision == "approved" else ""
    count = conn.execute(f"UPDATE ops.rule_proposal SET status = %s, reviewed_by = %s, reviewed_at = now() "
                         f"WHERE id = ANY (%s) AND status = 'proposed'{extra}", (decision, approver, ids)).rowcount
    conn.commit()
    return count
