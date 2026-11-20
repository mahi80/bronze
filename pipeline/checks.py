"""Observability: compare run signals with the thresholds in rules/thresholds.yaml."""
import logging

import psycopg
import yaml
from psycopg.types.json import Jsonb

from .config import Settings

log = logging.getLogger("pipeline")


def evaluate(conn: psycopg.Connection, settings: Settings) -> list[dict]:
    """The current value and level (ok, warn or fail) of every signal. Reads only."""
    signals = []
    for check in yaml.safe_load((settings.rules_dir / "thresholds.yaml").read_text(encoding="utf-8")):
        value = conn.execute(check["sql"]).fetchone()[0]
        value = float(value) if value is not None else 0.0
        level = "fail" if value > check["fail"] else "warn" if value > check["warn"] else "ok"
        signals.append({"signal": check["signal"], "description": check["description"], "value": value,
                        "warn": check["warn"], "fail": check["fail"], "level": level})
    return signals


def run(conn: psycopg.Connection, run_id: int, settings: Settings) -> str:
    """Returns the worst level seen: ok, warn or fail. Alerts are rows in ops.alert."""
    worst = "ok"
    for check in evaluate(conn, settings):
        signal, value, level = check["signal"], check["value"], check["level"]
        log.info("run=%s signal=%s value=%s level=%s", run_id, signal, value, level)
        if level != "ok":
            conn.execute("INSERT INTO ops.alert (run_id, signal, level, value, threshold, message) VALUES (%s, %s, %s, %s, %s, %s)",
                         (run_id, signal, level, value, check[level], check["description"]))
            if signal in ("single_reason_rejects", "unmapped_rows_pct"):
                conn.execute("INSERT INTO ops.drift_event (run_id, kind, detail) VALUES (%s, %s, %s)",
                             (run_id, signal, Jsonb({"value": value, "hint": "run the agents to propose rules or mappings"})))
            worst = "fail" if "fail" in (worst, level) else "warn"
    conn.commit()
    return worst
