"use client";

import { ArrowRight, CheckCheck, ClipboardCheck, Workflow } from "lucide-react";
import type { Overview, Run, Signal } from "../api";
import { count, dateLabel, duration, signalName, useApi } from "../api";
import type { Navigate } from "../console";
import { DataTable, EmptyState, LayerDot, Loading, Meter, Panel, StatusBadge } from "../ui";
import { UploadButton } from "../upload";
import type { Pipeline } from "./runs";
import { RunButton, runColumns } from "./runs";

export function OverviewView({ overview, refresh, pipeline, onNavigate, onChanged }: {
  overview: { data?: Overview; loading: boolean }; refresh: number; pipeline: Pipeline; onNavigate: Navigate; onChanged: () => void;
}) {
  const recent = useApi<{ runs: Run[] }>(overview.data?.initialised ? "runs?limit=5" : null, refresh);
  const data = overview.data;
  const heading = (
    <div className="page-heading">
      <div>
        <h1>Overview</h1>
        <p>What the last run loaded, what it set aside, and whether every row is accounted for.</p>
      </div>
      <div className="heading-actions"><UploadButton onChanged={onChanged} /><RunButton pipeline={pipeline} /></div>
    </div>
  );
  if (!data) return <>{heading}{overview.loading && <Loading />}</>;

  const rows = data.accounting;
  if (!rows) {
    return (
      <>
        {heading}
        <section className="panel">
          <EmptyState icon={Workflow} title="No run has completed in this database"
            description="Run the pipeline to load the files in data/ into bronze, then build silver and gold. It takes a couple of seconds and can be repeated safely."
            action={<RunButton pipeline={pipeline} />} />
        </section>
      </>
    );
  }

  const gold = data.gold ?? [];
  const pending = data.pending ?? { mappings: 0, rules: 0 };
  const waiting = pending.mappings + pending.rules;
  const last = data.last_run;
  return (
    <>
      {heading}
      <section className="flow" aria-label="Rows in each layer">
        <div className="flow-steps">
          <FlowStep layer="bronze" name="Bronze" figure={rows.bronze} unit="rows as delivered"
            note={`${count(data.files?.length ?? 0)} ${data.files?.length === 1 ? "file" : "files"}, every column kept as text`} />
          <FlowStep layer="silver" name="Silver" figure={rows.silver} unit="valid tickets"
            note={`${count(rows.rejected)} quarantined, ${count(rows.superseded)} superseded`} />
          <FlowStep layer="current" name="Current" figure={rows.silver - rows.duplicates} unit="unique tickets"
            note={`${count(rows.duplicates)} duplicates flagged, not deleted`} />
          <FlowStep layer="gold" name="Gold" figure={gold.reduce((total, model) => total + model.rows, 0)} unit={`rows in ${gold.length} models`}
            note="rebuilt from current tickets" />
        </div>
        <p className="flow-proof">
          <CheckCheck size={17} />
          <span>Every row is accounted for:</span>
          <code>{count(rows.bronze)} = {count(rows.silver)} + {count(rows.rejected)} + {count(rows.superseded)}</code>
          <span>(silver, quarantined, superseded). Run {rows.run_id} would have failed otherwise.</span>
        </p>
      </section>

      <div className="two-column">
        <Panel title="Checks" description="The signals in rules/thresholds.yaml, measured on the tables as they are now.">
          {(data.signals ?? []).map((signal) => <SignalRow key={signal.signal} signal={signal} />)}
        </Panel>
        <div>
          <Panel title="Waiting for review" count={waiting}>
            {waiting ? (
              <div className="panel-body">
                <dl className="key-value-list">
                  <div><dt>Mappings proposed</dt><dd>{count(pending.mappings)}</dd></div>
                  <div><dt>Rules proposed</dt><dd>{count(pending.rules)}</dd></div>
                </dl>
                <button className="button secondary" style={{ marginTop: 14 }} onClick={() => onNavigate("review")}>Open review<ArrowRight size={15} /></button>
              </div>
            ) : (
              <div className="queue-clear">
                <span><ClipboardCheck size={24} /></span>
                <h3>Nothing is waiting</h3>
                <p>Mappings and rules proposed by the agents appear here for a decision.</p>
              </div>
            )}
          </Panel>
          {last && (
            <Panel title="Last run" action={<button className="text-link" onClick={() => onNavigate("runs", { run: last.run_id })}>Open<ArrowRight size={15} /></button>}>
              <div className="panel-body">
                <dl className="key-value-list">
                  <div><dt>Run {last.run_id}</dt><dd><StatusBadge status={last.status} /></dd></div>
                  <div><dt>Started</dt><dd>{dateLabel(last.started_at)}</dd></div>
                  <div><dt>Took</dt><dd>{duration(last.duration_ms)}</dd></div>
                  <div><dt>Rule set</dt><dd><code>{last.rule_set_version ?? "—"}</code></dd></div>
                </dl>
              </div>
            </Panel>
          )}
        </div>
      </div>

      <Panel title="Recent runs" action={<button className="text-link" onClick={() => onNavigate("runs")}>All runs<ArrowRight size={15} /></button>}>
        <DataTable columns={runColumns((run) => onNavigate("runs", { run: run.run_id }))} rows={recent.data?.runs ?? []} rowKey={(run) => run.run_id} minWidth={620} />
      </Panel>
    </>
  );
}

function FlowStep({ layer, name, figure, unit, note }: { layer: "bronze" | "silver" | "current" | "gold"; name: string; figure: number; unit: string; note: string }) {
  return (
    <div className="flow-step">
      <span><LayerDot layer={layer} />{name}</span>
      <strong>{count(figure)}</strong>
      <p>{unit}</p>
      <small>{note}</small>
    </div>
  );
}

function SignalRow({ signal }: { signal: Signal }) {
  const [name, unit] = signalName(signal.signal);
  const tone = signal.level === "fail" ? "red" : signal.level === "warn" ? "amber" : "teal";
  return (
    <div className="signal">
      <div>
        <h3>{name}<StatusBadge status={signal.level} /></h3>
        <p>{signal.description}</p>
      </div>
      <div className="signal-value">
        <strong>{count(Number(signal.value.toFixed(2)))}{unit}</strong>
        <Meter share={signal.fail ? signal.value / signal.fail : 0} tone={tone} label={`${signal.value}${unit} against a limit of ${signal.fail}${unit}`} />
        <small>Warns above {signal.warn}{unit}, fails above {signal.fail}{unit}</small>
      </div>
    </div>
  );
}
