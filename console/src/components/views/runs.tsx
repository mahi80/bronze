"use client";

import { ArrowLeft, Check, CircleDot, LoaderCircle, Play, ShieldCheck, TriangleAlert, Workflow, X } from "lucide-react";
import { useState } from "react";
import type { Run, RunDetail, RuleEffect, Stage } from "../api";
import { count, dateLabel, duration, percent, runResult, signalName, useApi } from "../api";
import type { Navigate } from "../console";
import type { Column } from "../ui";
import { DataTable, EmptyState, Loading, Meter, Notice, Panel, StatusBadge, Tabs } from "../ui";

export interface Pipeline { run: () => void; busy: boolean }

export function RunButton({ pipeline, label = "Run pipeline" }: { pipeline: Pipeline; label?: string }) {
  return (
    <button className="button primary" onClick={pipeline.run} disabled={pipeline.busy}>
      {pipeline.busy ? <LoaderCircle size={16} className="spin" /> : <Play size={15} />}
      {pipeline.busy ? "Running…" : label}
    </button>
  );
}

export const runColumns = (onOpen: (run: Run) => void): Column<Run>[] => [
  { key: "run", label: "Run", render: (run) => <button className="table-title" onClick={() => onOpen(run)}>Run {run.run_id}</button> },
  { key: "started", label: "Started", render: (run) => <span className="nowrap">{dateLabel(run.started_at)}</span> },
  { key: "duration", label: "Took", align: "right", render: (run) => duration(run.duration_ms) },
  { key: "status", label: "Status", render: (run) => <StatusBadge status={run.status} /> },
  { key: "result", label: "Result", render: (run) => <span className="muted">{runResult(run)}</span> },
  { key: "rules", label: "Rule set", render: (run) => run.rule_set_version ? <code>{run.rule_set_version}</code> : "—" },
];

export function RunsView({ runId, refresh, pipeline, onNavigate }: { runId: number | null; refresh: number; pipeline: Pipeline; onNavigate: Navigate }) {
  const list = useApi<{ runs: Run[]; total: number }>(runId ? null : "runs?limit=100", refresh);
  if (runId) return <RunDetailView runId={runId} refresh={refresh} onBack={() => onNavigate("runs")} />;
  const runs = list.data?.runs ?? [];
  return (
    <>
      <div className="page-heading">
        <div>
          <h1>Runs</h1>
          <p>Each run loads new files into bronze and rebuilds silver and gold in one transaction per layer. Running again changes nothing unless the files or the rule set changed.</p>
        </div>
        <div className="heading-actions"><RunButton pipeline={pipeline} /></div>
      </div>
      {list.error && <Notice tone="danger">{list.error}</Notice>}
      {list.loading ? <Loading /> : (
        <Panel title="Run history" description={list.data && list.data.total > runs.length ? `Latest ${runs.length} of ${count(list.data.total)}` : undefined}>
          {runs.length
            ? <DataTable columns={runColumns((run) => onNavigate("runs", { run: run.run_id }))} rows={runs} rowKey={(run) => run.run_id} minWidth={620} />
            : <EmptyState icon={Workflow} title="No runs yet" description="Run the pipeline to load the source file and build the three layers." action={<RunButton pipeline={pipeline} />} />}
        </Panel>
      )}
    </>
  );
}

function RunDetailView({ runId, refresh, onBack }: { runId: number; refresh: number; onBack: () => void }) {
  const detail = useApi<RunDetail>(`runs/${runId}`, refresh);
  const [tab, setTab] = useState("stages");
  const back = <button className="back-link" onClick={onBack}><ArrowLeft size={16} />All runs</button>;
  if (detail.loading) return <>{back}<Loading label="Loading run" /></>;
  if (!detail.data) return <>{back}<Notice tone="danger">{detail.error || "This run is not available."}</Notice></>;

  const { run, stages, rules, alerts, drift, lineage } = detail.data;
  const stageColumns: Column<Stage>[] = [
    { key: "stage", label: "Stage", render: (stage) => <strong>{stage.stage}</strong> },
    { key: "status", label: "Status", render: (stage) => <StatusBadge status={stage.status} /> },
    { key: "in", label: "Rows in", align: "right", render: (stage) => count(stage.rows_in) },
    { key: "out", label: "Rows out", align: "right", render: (stage) => count(stage.rows_out) },
    { key: "rejected", label: "Quarantined", align: "right", render: (stage) => count(stage.rows_rejected) },
    { key: "time", label: "Took", align: "right", render: (stage) => duration(stage.duration_ms) },
    { key: "message", label: "Logged", render: (stage) => <code className="muted">{stage.message}</code> },
  ];
  const ruleColumns: Column<RuleEffect>[] = [
    { key: "id", label: "ID", render: (rule) => <span className="rule-id">{rule.rule_id}</span>, sortBy: (rule) => rule.rule_id },
    { key: "title", label: "Rule", render: (rule) => rule.title },
    { key: "affected", label: "Rows affected", align: "right", render: (rule) => count(rule.rows_affected), sortBy: (rule) => rule.rows_affected },
    { key: "share", label: "Share of rows checked", align: "right", sortBy: (rule) => rule.pct, render: (rule) => (
      <span className="meter-cell">{percent(rule.pct, 2)}<Meter share={(rule.pct ?? 0) / 100} label={`${rule.pct ?? 0}% of ${rule.rows_checked} rows`} /></span>
    ) },
  ];

  return (
    <>
      {back}
      <div className="page-heading">
        <div>
          <div className="heading-line"><h1>Run {run.run_id}</h1><StatusBadge status={run.status} /></div>
          <p>Started {dateLabel(run.started_at)}, took {duration(run.duration_ms)}.{run.rule_set_version && <> Built with rule set <code>{run.rule_set_version}</code>.</>}</p>
        </div>
      </div>
      {detail.error && <Notice tone="danger">{detail.error}</Notice>}
      {run.status === "failed" && <Notice tone="danger"><strong>This run failed.</strong> {run.message} The tables of the previous run were left in place.</Notice>}
      <section className="panel"><Lifecycle run={run} stages={stages} alerts={alerts.length} /></section>

      <Tabs label="Run details" current={tab} onChange={setTab} tabs={[
        { id: "stages", label: "Stages" }, { id: "rules", label: `Rule effects (${rules.length})` },
        { id: "alerts", label: `Alerts and drift (${alerts.length + drift.length})` }, { id: "lineage", label: "Lineage" },
      ]} />
      {tab === "stages" && <section className="panel"><DataTable stack columns={stageColumns} rows={stages} rowKey={(stage) => stage.stage} minWidth={760} /></section>}
      {tab === "rules" && (
        <Panel title="Measured effect of every rule" description="Recorded by this run in ops.dq_results, so the documented rule and its effect cannot drift apart.">
          {rules.length ? <DataTable columns={ruleColumns} rows={rules} rowKey={(rule) => rule.rule_id} minWidth={640} />
            : <EmptyState icon={ShieldCheck} title="No rule effects recorded" description="Rule effects are measured by the silver stage. This run did not complete it." />}
        </Panel>
      )}
      {tab === "alerts" && (
        <section className="panel">
          {alerts.length + drift.length === 0 && <EmptyState icon={ShieldCheck} title="No check crossed its limit" description="Reject rate, single-reason rejects, duplicates, unmapped values and unmatched notes were all within their thresholds." />}
          {alerts.length > 0 && <DataTable rows={alerts} rowKey={(alert) => alert.signal} minWidth={620} columns={[
            { key: "signal", label: "Signal", render: (alert) => <strong>{signalName(alert.signal)[0]}</strong> },
            { key: "level", label: "Level", render: (alert) => <StatusBadge status={alert.level} /> },
            { key: "value", label: "Value", align: "right", render: (alert) => `${count(Number(alert.value.toFixed(2)))}${signalName(alert.signal)[1]}` },
            { key: "threshold", label: "Limit", align: "right", render: (alert) => `${count(alert.threshold)}${signalName(alert.signal)[1]}` },
            { key: "message", label: "What it measures", render: (alert) => alert.message },
          ]} />}
          {drift.map((event, index) => (
            <div className="proposal" key={index}>
              <h3><TriangleAlert size={16} /> Drift signal: {signalName(event.kind)[0].toLowerCase()}{event.source_file && <> in <code>{event.source_file}</code></>}</h3>
              <pre className="code-block">{JSON.stringify(event.detail, null, 1)}</pre>
            </div>
          ))}
        </section>
      )}
      {tab === "lineage" && (
        <Panel title="What this run read and wrote" description="One row per source and target in ops.lineage_edge, with the transformation and the rule set it used.">
          <DataTable rows={lineage} rowKey={(edge) => `${edge.source_object}>${edge.target_object}`} minWidth={720} columns={[
            { key: "source", label: "Read from", render: (edge) => <code>{edge.source_object}</code> },
            { key: "target", label: "Wrote to", render: (edge) => <code>{edge.target_object}</code> },
            { key: "how", label: "Transformation", render: (edge) => edge.transformation },
            { key: "rules", label: "Rule set", render: (edge) => edge.rule_set_version ? <code>{edge.rule_set_version}</code> : "—" },
            { key: "who", label: "Executed by", render: (edge) => edge.executed_by },
          ]} />
        </Panel>
      )}
    </>
  );
}

function Lifecycle({ run, stages, alerts }: { run: Run; stages: Stage[]; alerts: number }) {
  const checks = run.message?.match(/^checks=(ok|warn|fail)$/)?.[1];
  const steps = ["bronze", "silver", "gold"].map((name) => {
    const stage = stages.find((item) => item.stage === name);
    const note = !stage ? "Not reached" : stage.status === "failed" ? "Failed, rolled back"
      : name === "bronze" ? `${count(stage.rows_out)} new rows` : name === "silver" ? `${count(stage.rows_out)} tickets` : `${count(stage.rows_out)} rows`;
    return { name: name.charAt(0).toUpperCase() + name.slice(1), state: !stage ? "" : stage.status === "failed" ? "failed" : "done", note };
  });
  steps.push({
    name: "Checks",
    state: !checks ? "" : checks === "ok" ? "done" : "warned",
    note: !checks ? "Not reached" : checks === "ok" ? "Within limits" : `${alerts} ${alerts === 1 ? "alert" : "alerts"}`,
  });
  return (
    <div className="lifecycle">
      {steps.map((step) => (
        <div key={step.name} className={step.state}>
          <span>{step.state === "done" ? <Check size={16} /> : step.state === "failed" ? <X size={16} /> : step.state === "warned" ? <TriangleAlert size={15} /> : <CircleDot size={16} />}</span>
          <strong>{step.name}<small>{step.note}</small></strong>
        </div>
      ))}
    </div>
  );
}
