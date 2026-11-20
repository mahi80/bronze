-- 30_gold.sql
-- Rebuilds the three gold models from silver.tickets_current (duplicates excluded). One transaction.
-- Every rate is published with its coverage, because several source fields are unreliable (see README).

-- 1. SLA performance by month, category and priority.
TRUNCATE gold.sla_performance_monthly;
INSERT INTO gold.sla_performance_monthly
    (month, category, priority, tickets_opened, tickets_valid_interval, tickets_measurable, tickets_within_sla,
     median_resolution_hours, p90_resolution_hours, pct_within_sla, pct_measurable, _run_id)
SELECT date_trunc('month', created_at)::date,
       category_std,
       coalesce(priority_std, 'Unspecified'),
       count(*),
       count(resolution_hours),
       count(*) FILTER (WHERE resolution_hours IS NOT NULL AND sla_hours IS NOT NULL),
       count(*) FILTER (WHERE resolution_hours <= sla_hours),
       round((percentile_cont(0.5) WITHIN GROUP (ORDER BY resolution_hours))::numeric, 2),
       round((percentile_cont(0.9) WITHIN GROUP (ORDER BY resolution_hours))::numeric, 2),
       round(100.0 * count(*) FILTER (WHERE resolution_hours <= sla_hours)
             / nullif(count(*) FILTER (WHERE resolution_hours IS NOT NULL AND sla_hours IS NOT NULL), 0), 2),
       round(100.0 * count(*) FILTER (WHERE resolution_hours IS NOT NULL AND sla_hours IS NOT NULL) / count(*), 2),
       current_setting('app.run_id')::bigint
FROM silver.tickets_current
GROUP BY 1, 2, 3;

-- 2. Where problems cluster: building by category.
TRUNCATE gold.building_category_hotspots;
INSERT INTO gold.building_category_hotspots
    (building, category, tickets, open_backlog, hazard_tickets, hazard_low_or_no_priority, pct_of_building,
     rank_in_building, _run_id)
WITH base AS (
    SELECT building,
           category_std AS category,
           count(*) AS tickets,
           count(*) FILTER (WHERE NOT is_terminal) AS open_backlog,
           count(*) FILTER (WHERE hazard_signal = 'safety_hazard') AS hazard_tickets,
           count(*) FILTER (WHERE hazard_signal = 'safety_hazard' AND coalesce(priority_std, 'Low') = 'Low') AS hazard_low_or_no_priority
    FROM silver.tickets_current
    GROUP BY 1, 2
)
SELECT building, category, tickets, open_backlog, hazard_tickets, hazard_low_or_no_priority,
       round(100.0 * tickets / sum(tickets) OVER (PARTITION BY building), 2),
       rank() OVER (PARTITION BY building ORDER BY tickets DESC, category),
       current_setting('app.run_id')::bigint
FROM base;

-- 3. Workload and spend by assignee and month. Unassigned tickets are their own row, not hidden.
TRUNCATE gold.assignee_workload_cost;
INSERT INTO gold.assignee_workload_cost
    (assignee, month, tickets, open_backlog, tickets_with_cost, cost_total, cost_median, pct_cost_known, _run_id)
SELECT coalesce(assigned_to, '(unassigned)'),
       date_trunc('month', created_at)::date,
       count(*),
       count(*) FILTER (WHERE NOT is_terminal),
       count(cost_amount),
       sum(cost_amount),
       round((percentile_cont(0.5) WITHIN GROUP (ORDER BY cost_amount))::numeric, 2),
       round(100.0 * count(cost_amount) / count(*), 2),
       current_setting('app.run_id')::bigint
FROM silver.tickets_current
GROUP BY 1, 2;
