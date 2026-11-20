"use client";

import { Search } from "lucide-react";
import { useState } from "react";
import type { ReactNode } from "react";
import type { Cell, GoldTable } from "../api";
import { count, percent, useApi } from "../api";
import type { Column } from "../ui";
import { DataTable, Loading, Meter, Notice, Panel, Tabs } from "../ui";

type Row = Record<string, Cell>;
type Kind = "text" | "month" | "count" | "decimal" | "share";

// What each model is for and how to read it. The same words as the README, so the console and the document agree.
const MODELS: { id: string; label: string; grain: string; note: string; columns: [string, string, Kind][] }[] = [
  {
    id: "sla_performance_monthly", label: "SLA performance", grain: "month × category × priority",
    note: "Read the rate with its coverage. A ticket is measurable only when its resolved time is not before its created time and it has a real SLA, which is true for a minority of tickets in this file.",
    columns: [["month", "Month", "month"], ["category", "Category", "text"], ["priority", "Priority", "text"], ["tickets_opened", "Opened", "count"],
      ["tickets_measurable", "Measurable", "count"], ["tickets_within_sla", "Within SLA", "count"], ["median_resolution_hours", "Median hours", "decimal"],
      ["p90_resolution_hours", "90th pct hours", "decimal"], ["pct_within_sla", "Within SLA, of measurable", "share"], ["pct_measurable", "Coverage", "share"]],
  },
  {
    id: "building_category_hotspots", label: "Building hotspots", grain: "building × category",
    note: "Where problems cluster. It rests on counts, building and category, the reliable fields, and adds the safety-hazard tickets that are Low priority or have none.",
    columns: [["building", "Building", "text"], ["category", "Category", "text"], ["tickets", "Tickets", "count"], ["open_backlog", "Open", "count"],
      ["hazard_tickets", "Safety hazards", "count"], ["hazard_low_or_no_priority", "Hazards at Low or no priority", "count"],
      ["pct_of_building", "Share of building", "share"], ["rank_in_building", "Rank in building", "count"]],
  },
  {
    id: "assignee_workload_cost", label: "Assignee workload and cost", grain: "assignee × month",
    note: "Workload and spend per assignee. Cost is not known for every ticket, so coverage is a column, and unassigned tickets are their own row rather than hidden.",
    columns: [["assignee", "Assignee", "text"], ["month", "Month", "month"], ["tickets", "Tickets", "count"], ["open_backlog", "Open", "count"],
      ["tickets_with_cost", "With a cost", "count"], ["cost_total", "Cost total", "decimal"], ["cost_median", "Cost median", "decimal"],
      ["pct_cost_known", "Cost coverage", "share"]],
  },
];

const monthFormat = new Intl.DateTimeFormat("en-US", { month: "short", year: "numeric", timeZone: "UTC" });
const decimalFormat = new Intl.NumberFormat("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 2 });

const monthLabel = (value: Cell) => monthFormat.format(new Date(`${value}T00:00:00Z`));

function show(value: Cell, kind: Kind, label: string): ReactNode {
  if (value === null) return <span className="muted">—</span>;
  if (kind === "month") return <span className="nowrap">{monthLabel(value)}</span>;
  if (kind === "count") return count(Number(value));
  if (kind === "decimal") return decimalFormat.format(Number(value));
  if (kind === "share") return <span className="meter-cell">{percent(Number(value))}<Meter share={Number(value) / 100} label={`${label} ${value}%`} /></span>;
  return String(value);
}

export function GoldView({ refresh }: { refresh: number }) {
  const [model, setModel] = useState(MODELS[0]);
  const [search, setSearch] = useState("");
  const table = useApi<GoldTable>(`gold/${model.id}`, refresh);
  const needle = search.trim().toLowerCase();
  const textColumns = model.columns.filter(([, , kind]) => kind === "text" || kind === "month");
  const rows = (table.data?.rows ?? []).filter((row) => !needle || textColumns.some(([key, , kind]) => String(kind === "month" ? monthLabel(row[key]) : row[key]).toLowerCase().includes(needle)));
  const columns: Column<Row>[] = model.columns.map(([key, label, kind]) => ({
    key, label, align: kind === "text" || kind === "month" ? undefined : "right",
    sortBy: (row) => typeof row[key] === "string" || typeof row[key] === "number" ? row[key] as string | number : null,
    render: (row) => show(row[key], kind, label),
  }));

  return (
    <>
      <div className="page-heading">
        <div>
          <h1>Gold</h1>
          <p>Three models, rebuilt on every run from current tickets (duplicates left out). Each ticket is counted exactly once per model, and every rate sits beside its coverage.</p>
        </div>
      </div>
      <Tabs label="Gold models" current={model.id} tabs={MODELS} onChange={(id) => { setModel(MODELS.find((item) => item.id === id) ?? MODELS[0]); setSearch(""); }} />
      {table.error && <Notice tone="danger">{table.error}</Notice>}
      <Panel title={`gold.${model.id}`} description={`One row per ${model.grain}`}>
        <div className="model-note"><p>{model.note}</p></div>
        <div className="toolbar">
          <label className="search-field"><Search size={16} /><input aria-label="Filter rows" placeholder="Filter by name or month" value={search} onChange={(event) => setSearch(event.target.value)} /></label>
          <span>{count(rows.length)} of {count(table.data?.total)} rows. Select a column heading to sort.</span>
        </div>
        {table.loading ? <Loading /> : <DataTable key={model.id} columns={columns} rows={rows} pageSize={25} rowKey={(row) => model.columns.slice(0, 3).map(([key]) => row[key]).join("|")} minWidth={900} />}
      </Panel>
    </>
  );
}
