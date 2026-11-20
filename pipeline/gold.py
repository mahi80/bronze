"""Gold: rebuild the three business models from silver.tickets_current."""
import psycopg

from . import db

MODELS = ["gold.sla_performance_monthly", "gold.building_category_hotspots", "gold.assignee_workload_cost"]


def run(conn: psycopg.Connection, run_id: int, rule_set_version: str) -> dict:
    with db.stage(conn, run_id, "gold") as counts:
        conn.execute("SELECT set_config('app.run_id', %s, true)", (str(run_id),))
        db.run_sql_file(conn, "30_gold.sql")
        source_rows = conn.execute("SELECT count(*) FROM silver.tickets_current").fetchone()[0]
        sizes = {}
        for model in MODELS:
            sizes[model] = conn.execute(f"SELECT count(*) FROM {model}").fetchone()[0]
            db.lineage(conn, run_id, "silver.tickets_current", model, "sql/30_gold.sql", rule_set_version)
        # every ticket must be counted exactly once in each model
        for model, column in [("gold.sla_performance_monthly", "tickets_opened"), ("gold.building_category_hotspots", "tickets"),
                              ("gold.assignee_workload_cost", "tickets")]:
            total = conn.execute(f"SELECT coalesce(sum({column}), 0) FROM {model}").fetchone()[0]
            if total != source_rows:
                raise AssertionError(f"{model}: counts {total} tickets, silver.tickets_current has {source_rows}")
        counts.update(rows_in=source_rows, rows_out=sum(sizes.values()),
                      message=" ".join(f"{m.split('.')[1]}={n}" for m, n in sizes.items()))
    return sizes
