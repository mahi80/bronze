"use client";

import { Search } from "lucide-react";
import { useState } from "react";
import type { Quality, QualityRule } from "../api";
import { count, percent, useApi, words } from "../api";
import type { Navigate } from "../console";
import type { Column } from "../ui";
import { DataTable, Loading, Meter, Notice, Panel, Raw, Tabs } from "../ui";

export function QualityView({ refresh, onNavigate }: { refresh: number; onNavigate: Navigate }) {
  const quality = useApi<Quality>("quality", refresh);
  const [search, setSearch] = useState("");
  const [tab, setTab] = useState("rules");
  const heading = (
    <div className="page-heading">
      <div>
        <h1>Data quality</h1>
        <p>Every cleaning rule with the effect the pipeline measured, and everything that was set aside or flagged. Nothing is deleted: a row is cleaned, flagged or quarantined, and can be traced.</p>
      </div>
    </div>
  );
  if (!quality.data) return <>{heading}{quality.error ? <Notice tone="danger">{quality.error}</Notice> : <Loading />}</>;

  const data = quality.data;
  const silverRows = data.rules.find((rule) => rule.scope === "silver" && rule.rows_checked)?.rows_checked ?? 0;
  const quarantined = data.rejected.length;
  const duplicates = data.duplicates.reduce((total, item) => total + item.rows, 0);
  const needle = search.trim().toLowerCase();
  const rules = data.rules.filter((rule) => !needle || `${rule.id} ${rule.title} ${rule.rule} ${rule.why}`.toLowerCase().includes(needle));

  const ruleColumns: Column<QualityRule>[] = [
    { key: "id", label: "ID", sortBy: (rule) => rule.id, render: (rule) => <span className="rule-id">{rule.id}</span> },
    { key: "rule", label: "Rule", render: (rule) => (
      <>
        <strong>{rule.title}</strong>{rule.origin === "agent" && <span className="pill" style={{ marginLeft: 8 }}>Approved agent check</span>}
        <span className="table-subtitle">{rule.rule}</span>
        {rule.why && <details className="table-subtitle"><summary>Why this rule</summary>{rule.why}</details>}
      </>
    ) },
    { key: "affected", label: "Rows affected", align: "right", sortBy: (rule) => rule.rows_affected, render: (rule) => count(rule.rows_affected) },
    { key: "share", label: "Share of rows checked", align: "right", sortBy: (rule) => rule.pct, render: (rule) => (
      <span className="meter-cell">{percent(rule.pct, 2)}<Meter share={(rule.pct ?? 0) / 100} label={`${rule.pct ?? 0}% of ${rule.rows_checked ?? 0} ${rule.scope} rows`} /></span>
    ) },
  ];

  return (
    <>
      {heading}
      {quality.error && <Notice tone="danger">{quality.error}</Notice>}
      {data.run_id === null && <Notice>No rule effects have been measured yet. Run the pipeline first.</Notice>}

      <div className="stat-trio">
        <Panel title="Quarantined rows" count={quarantined} description="Reasons per row. A row can fail several checks.">
          <BarList items={data.reject_reasons.map((item) => ({ name: words(item.reason), rows: item.rows }))} total={quarantined} empty="No row failed a hard check." />
        </Panel>
        <Panel title="Duplicates flagged" count={duplicates} description="By how strong the match is. Gold leaves these out.">
          <BarList items={data.duplicates.map((item) => ({ name: words(item.strength), rows: item.rows }))} total={duplicates} empty="No duplicate tickets found." />
        </Panel>
        <Panel title="Flags on valid tickets" description={`Share of the ${count(silverRows)} tickets in silver.`}>
          <BarList items={data.flags.map((item) => ({ name: words(item.flag), rows: item.rows }))} total={silverRows} empty="No flags raised." />
        </Panel>
      </div>

      <Tabs label="Data quality detail" current={tab} onChange={setTab} tabs={[
        { id: "rules", label: `Cleaning rules (${data.rules.length})` }, { id: "quarantine", label: `Quarantine (${quarantined})` },
        { id: "tags", label: `Column tags (${data.tags.length})` },
      ]} />

      {tab === "rules" && <Panel title="Cleaning rules" description={data.run_id === null ? undefined : `Measured by run ${data.run_id}. The rule text comes from rules/cleaning_rules.yaml, the counts from ops.dq_results.`}>
        <div className="toolbar">
          <label className="search-field"><Search size={16} /><input aria-label="Search rules" placeholder="Search rules" value={search} onChange={(event) => setSearch(event.target.value)} /></label>
          <span>{rules.length} of {data.rules.length} rules</span>
        </div>
        <DataTable stack columns={ruleColumns} rows={rules} rowKey={(rule) => rule.id} minWidth={720} />
      </Panel>}

      {tab === "quarantine" && <Panel title="Quarantine" count={quarantined} description="Rows kept out of silver, exactly as they arrived, with every reason. They stay in bronze and in silver.tickets_rejected.">
        <DataTable rows={data.rejected} rowKey={(row) => row.source_row} minWidth={880} columns={[
          { key: "row", label: "File row", align: "right", sortBy: (row) => row.source_row, render: (row) => count(row.source_row) },
          { key: "reasons", label: "Failed checks", render: (row) => row.reasons.map((reason) => <span className="tag danger" key={reason}>{words(reason)}</span>) },
          ...["ticket_id", "created_at", "status", "building", "description"].map((field): Column<Quality["rejected"][number]> => ({
            key: field, label: field, render: (row) => <Raw value={row.raw[field]} />,
          })),
        ]} />
        <div className="table-footer">
          To follow a single ticket through the layers, use <button className="text-link" onClick={() => onNavigate("trace")}>Ticket trace</button>.
        </div>
      </Panel>}

      {tab === "tags" && <Panel title="Column tags" description="Written when a file lands, from the shapes of the values. The profile the Data Quality Agent reads withholds the values of a personal-data column.">
        <DataTable rows={data.tags} rowKey={(tag) => tag.column_name} minWidth={620} columns={[
          { key: "column", label: "Source column", render: (tag) => <code>{tag.column_name}</code> },
          { key: "type", label: "Semantic type", render: (tag) => words(tag.semantic_type) },
          { key: "sensitivity", label: "Sensitivity", render: (tag) => tag.sensitivity === "none" ? <span className="muted">None</span> : <span className="tag warning">{words(tag.sensitivity)}</span> },
          { key: "basis", label: "Decided from", render: (tag) => <span className="muted">{tag.basis}</span> },
        ]} />
      </Panel>}
    </>
  );
}

function BarList({ items, total, empty }: { items: { name: string; rows: number }[]; total: number; empty: string }) {
  if (!items.length) return <div className="panel-body muted">{empty}</div>;
  return (
    <div className="bar-list">
      {items.map((item) => (
        <div key={item.name}>
          <span>{item.name}</span>
          <strong>{count(item.rows)}</strong>
          <Meter share={total ? item.rows / total : 0} label={`${item.rows} of ${total}`} />
        </div>
      ))}
    </div>
  );
}
