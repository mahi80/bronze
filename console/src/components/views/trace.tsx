"use client";

// One ticket through the layers: the row as it arrived, the row silver built from it, and whether gold counts it.
// The source-to-target card follows the lineage view of my DataMigration console.
import { ArrowRight, Route, Search } from "lucide-react";
import { useState } from "react";
import type { FormEvent, ReactNode } from "react";
import type { Cell, Trace } from "../api";
import { count, useApi, words } from "../api";
import type { Navigate } from "../console";
import { DataTable, EmptyState, LayerDot, Loading, Notice, Panel, Raw } from "../ui";

type Row = Record<string, Cell>;
const EXAMPLES = [["TKT-7113", "an original with one copy"], ["TKT-11000", "a re-keyed duplicate"], ["TKT-11231", "a swapped, cut-off copy"]];
const VALID_ID = /^[A-Za-z0-9-]{1,40}$/;

const has = (silver: Row, flag: string) => Array.isArray(silver.dq_flags) && silver.dq_flags.includes(flag);
const text = (value: Cell) => value === null ? null : String(value).replace("T", " ");

// For each source column: what silver holds for it, and what the pipeline did to get there.
const FIELDS: { column: string; value: (s: Row) => Cell; note: (s: Row) => string | null }[] = [
  { column: "ticket_id", value: (s) => s.ticket_id, note: () => null },
  { column: "created_at", value: (s) => text(s.created_at), note: (s) => `Read as ${s.created_at_format}, ${s.created_at_precision} precision` },
  { column: "resolved_at", value: (s) => text(s.resolved_at), note: (s) => has(s, "resolved_at_unparseable") ? "Not in a registered format; kept empty and flagged"
    : has(s, "resolved_before_created") ? "Earlier than created: both values kept, no duration calculated"
    : s.resolution_hours !== null ? `${s.resolution_hours} hours after created` : null },
  { column: "category", value: (s) => s.category_std, note: (s) => has(s, "swapped_columns") ? "Category and description were swapped in the file; repaired"
    : has(s, "category_unmapped") ? "No approved mapping for this spelling; labelled Unclassified"
    : s.category_raw !== s.category_std ? `Standardised from "${s.category_raw}"` : null },
  { column: "priority", value: (s) => s.priority_std, note: (s) => has(s, "priority_missing") ? "Missing in the file; not imputed"
    : has(s, "priority_unmapped") ? "No approved mapping for this value"
    : s.priority_raw !== s.priority_std ? `Standardised from "${s.priority_raw}"` : null },
  { column: "status", value: (s) => s.status, note: (s) => s.is_terminal ? "A closing status" : "Still open" },
  { column: "building", value: (s) => s.building, note: () => null },
  { column: "description", value: (s) => s.description, note: (s) => s.issue_type ? `Issue type "${s.issue_type}", hazard signal "${s.hazard_signal}"` : "No issue type mapped for this text" },
  { column: "submitted_by", value: (s) => s.submitter_name, note: (s) => has(s, "submitter_unmapped") ? "No approved mapping for this name; left unresolved"
    : s.submitter_id === null ? null
    : s.submitted_by_raw !== s.submitter_name ? `Resolved from "${s.submitted_by_raw}" to person ${s.submitter_id}` : `Person ${s.submitter_id}` },
  { column: "assigned_to", value: (s) => s.assigned_to, note: (s) => s.assigned_to === null ? "Blank in the file; not filled from the notes" : null },
  { column: "resolution_notes", value: (s) => s.resolution_notes, note: (s) => s.note_type === null ? null
    : s.note_type === "other" ? "Matches none of the registered note patterns" : [
    `Note type "${s.note_type}"`, s.root_cause && `root cause "${s.root_cause}"`, s.part_replaced && `part "${s.part_replaced}"`,
    s.hours_on_site !== null && `${s.hours_on_site} hours on site`, s.parts_eta_days !== null && `parts in ${s.parts_eta_days} days`,
    s.scheduled_weekday && `scheduled ${s.scheduled_weekday}`, s.resolved_by_note && `resolved by ${s.resolved_by_note}`,
    s.duplicate_of_ref && `refers to ${s.duplicate_of_ref}`,
  ].filter(Boolean).join(", ") },
  { column: "cost", value: (s) => s.cost_amount, note: (s) => `Cost status: ${words(String(s.cost_status)).toLowerCase()}` },
  { column: "sla_hours", value: (s) => s.sla_hours, note: (s) => `SLA status: ${words(String(s.sla_status)).toLowerCase()}` },
];

export function TraceView({ ticket, refresh, onNavigate }: { ticket: string | null; refresh: number; onNavigate: Navigate }) {
  const [input, setInput] = useState(ticket ?? "");
  const [shownFor, setShownFor] = useState(ticket);
  if (ticket !== shownFor) { setShownFor(ticket); setInput(ticket ?? ""); }        // follow the URL when it changes
  const valid = ticket !== null && VALID_ID.test(ticket);
  const trace = useApi<Trace>(valid ? `tickets/${ticket}` : null, refresh);
  const open = (id: string) => onNavigate("trace", { ticket: id });

  function submit(event: FormEvent<HTMLFormElement>) {
    event.preventDefault();
    if (input.trim()) open(input.trim().toUpperCase());
  }

  return (
    <>
      <div className="page-heading">
        <div>
          <h1>Ticket trace</h1>
          <p>Follow one ticket from the line in the file to the row gold reads: the value as delivered, the value in silver, and what the pipeline did in between.</p>
        </div>
      </div>
      <Panel title="Find a ticket">
        <div className="panel-body">
          <form className="trace-search" onSubmit={submit}>
            <label className="search-field"><Search size={16} /><input aria-label="Ticket ID" placeholder="TKT-1234" value={input} onChange={(event) => setInput(event.target.value)} /></label>
            <button className="button primary" type="submit" disabled={!input.trim()}>Trace</button>
          </form>
          <div className="chips">
            From the supplied file:
            {EXAMPLES.map(([id, what]) => <button key={id} className="chip" onClick={() => open(id)}>{id}, {what}</button>)}
          </div>
        </div>
      </Panel>
      {ticket !== null && !valid && <Notice tone="warning">Ticket IDs are letters, digits and dashes, such as TKT-1234. Quarantined placeholder rows are listed under Data quality.</Notice>}
      {trace.error && <Notice tone={trace.data ? "danger" : "warning"}>{trace.error}</Notice>}
      {trace.loading && <Loading label="Tracing" />}
      {trace.data ? <TraceResult trace={trace.data} onOpen={open} />
        : !ticket && <section className="panel"><EmptyState icon={Route} title="Pick a ticket to trace" description="Every silver row carries the hash of the bronze row it was built from, the run that built it and the rule set version, so any figure can be walked back to a line in the file." /></section>}
    </>
  );
}

function TraceResult({ trace, onOpen }: { trace: Trace; onOpen: (id: string) => void }) {
  const silver = trace.silver;
  const source = trace.bronze.find((row) => row._row_hash === silver?._row_hash) ?? trace.bronze[trace.bronze.length - 1];
  const reasons = Array.isArray(source?.reject_reasons) ? source.reject_reasons : [];
  const flags = silver && Array.isArray(silver.dq_flags) ? silver.dq_flags : [];
  const link = (id: Cell) => <button className="text-link" onClick={() => onOpen(String(id))}>{String(id)}</button>;
  return (
    <>
      {!silver && (
        <Notice tone="warning">
          <strong>Quarantined.</strong> This row is in bronze but not in silver, because it failed: {reasons.map((reason) => words(reason).toLowerCase()).join(", ") || "a hard check"}.
        </Notice>
      )}
      {silver?.is_duplicate && (
        <Notice tone="warning">
          <strong>Flagged as a duplicate</strong> of {link(silver.duplicate_of)} ({words(String(silver.dup_match_strength)).toLowerCase()} match). It stays in silver with the flag, and gold leaves it out.
        </Notice>
      )}
      {trace.copies.length > 0 && (
        <Notice>
          {count(trace.copies.length)} later {trace.copies.length === 1 ? "row is a copy" : "rows are copies"} of this ticket and {trace.copies.length === 1 ? "is" : "are"} flagged as duplicates:
          {trace.copies.map((copy) => <span key={copy.ticket_id}> {link(copy.ticket_id)}</span>)}
        </Notice>
      )}
      {trace.bronze.length > 1 && <Notice>This ticket was delivered {trace.bronze.length} times. Silver is built from the latest delivery, and every delivery stays in bronze.</Notice>}

      <Panel title={trace.ticket_id} description={flags.length ? undefined : silver ? "No data-quality flags on this ticket." : undefined}
        action={flags.length ? <span>{flags.map((flag) => <span className="tag warning" key={flag}>{words(flag)}</span>)}</span> : undefined}>
        <div className="lineage-route">
          <div>
            <small><LayerDot layer="bronze" />Bronze, as delivered</small>
            <strong>{source ? <>{String(source._source_file)}, row {count(Number(source._source_row_number))}</> : "No bronze row found"}</strong>
            {source && <p>Row hash <code>{String(source._row_hash).slice(0, 16)}</code>, loaded by run {String(source._batch_id)}</p>}
          </div>
          <ArrowRight size={20} />
          <div>
            <small><LayerDot layer="silver" />Silver, cleaned</small>
            <strong>{silver ? <>Built by run {String(silver._run_id)}</> : "Not in silver"}</strong>
            <p>{silver ? <>Rule set <code>{String(silver._rule_set_version)}</code></> : "Kept in silver.tickets_rejected with its reasons"}</p>
          </div>
          <ArrowRight size={20} />
          <div>
            <small><LayerDot layer="gold" />Gold</small>
            <strong>{!silver ? "Not counted" : silver.is_duplicate ? "Left out" : "Counted once per model"}</strong>
            <p>{!silver ? "Quarantined rows never reach gold" : silver.is_duplicate ? "Gold reads silver.tickets_current, which excludes duplicates" : "Through silver.tickets_current"}</p>
          </div>
        </div>
        {source && (
          <DataTable stack rows={FIELDS} rowKey={(field) => field.column} minWidth={760} columns={[
            { key: "column", label: "Source column", render: (field) => <code>{field.column}</code> },
            { key: "bronze", label: "As delivered", render: (field) => <Raw value={source[field.column]} /> },
            { key: "silver", label: "In silver", render: (field) => !silver ? <span className="muted">—</span> : <SilverValue value={field.value(silver)} note={field.note(silver)} /> },
          ]} />
        )}
      </Panel>
    </>
  );
}

function SilverValue({ value, note }: { value: Cell; note: string | null }): ReactNode {
  return (
    <>
      {value === null ? <span className="muted">NULL</span> : <span className="silver-value">{String(value)}</span>}
      {note && <span className="silver-note">{note}</span>}
    </>
  );
}
