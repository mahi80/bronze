"use client";

import { ArrowRight, Bot, LoaderCircle, Play } from "lucide-react";
import { useEffect, useState } from "react";
import type { AgentJob, Agents } from "../api";
import { api, count, dateLabel, errorMessage, useApi } from "../api";
import type { Navigate } from "../console";
import type { Column } from "../ui";
import { ConfirmDialog, DataTable, EmptyState, Loading, Notice, Panel, StatusBadge } from "../ui";

const TASK_NAMES: Record<string, string> = { category: "Category spellings", person: "Submitter names", issue: "Description templates" };
const AGENT_NAMES = { classify: "Semantic Classification Agent", dq: "Data Quality Agent" };

export function AgentsView({ refresh, onChanged, onNavigate }: { refresh: number; onChanged: () => void; onNavigate: Navigate }) {
  const agents = useApi<Agents>("agents", refresh);
  const [job, setJob] = useState<AgentJob | null>(null);          // the job as last polled; null until this page starts or sees one
  const [asking, setAsking] = useState<"classify" | "dq" | null>(null);
  const [starting, setStarting] = useState(false);
  const [error, setError] = useState("");
  const current = job ?? agents.data?.job ?? { status: "idle" };
  const running = current.status === "running";

  // Agent runs take minutes, so the API runs them in the background and this page asks how it is going.
  useEffect(() => {
    if (!running) return;
    const timer = window.setInterval(async () => {
      try {
        const next = await api<AgentJob>("agents/job");
        setJob(next);
        if (next.status !== "running") onChanged();
      } catch (failure) {
        setError(errorMessage(failure));
      }
    }, 2500);
    return () => window.clearInterval(timer);
  }, [running, onChanged]);

  async function start(agent: "classify" | "dq") {
    setStarting(true);
    setError("");
    try {
      setJob(await api<AgentJob>(`agents/${agent}`, { task: "all" }));
    } catch (failure) {
      setError(errorMessage(failure));
    } finally {
      setStarting(false);
      setAsking(null);
    }
  }

  const heading = (
    <div className="page-heading">
      <div>
        <h1>Agents</h1>
        <p>Two agents write proposals; neither writes to the ticket tables. Start one here, then decide on what it proposed under Review. The pipeline runs the same with or without them.</p>
      </div>
    </div>
  );
  if (!agents.data) return <>{heading}{agents.error ? <Notice tone="danger">{agents.error}</Notice> : <Loading />}</>;

  const data = agents.data;
  const toSend = data.unmapped.reduce((total, item) => total + item.to_send, 0);
  const blocked = !data.enabled || running || starting;
  return (
    <>
      {heading}
      {error && <Notice tone="danger">{error}</Notice>}
      {!data.enabled && (
        <Notice>
          <strong>No model provider is configured</strong> (<code>LLM_PROVIDER</code> is <code>{data.provider}</code>), so the agents cannot be started.
          To use them, set <code>LLM_PROVIDER</code> and <code>LLM_API_KEY</code> in <code>.env</code> and restart the API.
        </Notice>
      )}
      {current.status !== "idle" && <JobPanel job={current} onReview={() => onNavigate("review")} />}

      <div className="two-column">
        <Panel title={AGENT_NAMES.classify} description="Maps values it has not seen before: category spellings, submitter names, description templates."
          action={<StartButton label="Propose mappings" disabled={blocked || toSend === 0} onClick={() => setAsking("classify")} />}>
          <DataTable rows={data.unmapped} rowKey={(item) => item.task} columns={[
            { key: "task", label: "Values with no mapping yet", render: (item) => <strong>{TASK_NAMES[item.task] ?? item.task}</strong> },
            { key: "send", label: "Would be sent", align: "right", render: (item) => count(item.to_send) },
            { key: "held", label: `Held (on fewer than ${data.min_rows} rows)`, align: "right", render: (item) => count(item.held) },
          ]} />
          <div className="table-footer">
            {toSend === 0 ? "Every value in the loaded files already has a mapping, so there is nothing to send." : "Only distinct values are sent, never rows. In description text, numbers, building names and asset IDs are masked first."}
          </div>
        </Panel>
        <Panel title={AGENT_NAMES.dq} description="Reads a statistical profile of bronze and proposes rules with SQL."
          action={<StartButton label="Propose rules" disabled={blocked} onClick={() => setAsking("dq")} />}>
          <div className="panel-body">
            <dl className="key-value-list">
              <div><dt>Investigates</dt><dd>Each column, then relationships between columns</dd></div>
              <div><dt>May run</dt><dd>Read-only probe queries on bronze, at most 20 rows each</dd></div>
              <div><dt>Is checked by</dt><dd>Running its SQL and comparing the row count it claimed</dd></div>
            </dl>
          </div>
        </Panel>
      </div>

      <div className="two-column">
        <Panel title="Model usage" description="Live calls recorded in ops.agent_call. Calls made by the test stub or replayed from recordings are not counted.">
          {data.calls.length ? (
            <DataTable rows={data.calls} rowKey={(call) => `${call.task}|${call.model}`} minWidth={640} columns={[
              { key: "task", label: "Task", render: (call) => <strong>{call.task}</strong> },
              { key: "model", label: "Model", render: (call) => <code>{call.model}</code> },
              { key: "calls", label: "Calls", align: "right", render: (call) => count(call.calls) },
              { key: "in", label: "Tokens in", align: "right", render: (call) => count(call.tokens_in) },
              { key: "out", label: "Tokens out", align: "right", render: (call) => count(call.tokens_out) },
              { key: "latency", label: "Mean latency", align: "right", render: (call) => `${count(call.mean_latency_ms)} ms` },
              { key: "cost", label: "Estimated cost", align: "right", render: (call) => call.cost_usd === null ? "Price not listed" : `$${call.cost_usd.toFixed(4)}` },
            ]} />
          ) : <EmptyState icon={Bot} title="No live model call recorded" description="Nothing has been sent to a model from this database, so there is no usage or cost to show." />}
        </Panel>
        <Panel title="Settings">
          <div className="panel-body">
            <dl className="key-value-list">
              <div><dt>Provider</dt><dd>{data.provider}</dd></div>
              <div><dt>Classification model</dt><dd>{data.models.classification ?? "Not set"}</dd></div>
              <div><dt>Rule proposal model</dt><dd>{data.models.rule_proposal ?? "Not set"}</dd></div>
              <div><dt>Token budget per run</dt><dd>{count(data.token_budget)}</dd></div>
              <div><dt>Applied without waiting</dt><dd>Confidence {data.auto_apply_confidence} or more; never a person merge</dd></div>
            </dl>
          </div>
        </Panel>
      </div>

      {asking && (
        <ConfirmDialog busy={starting} onCancel={() => setAsking(null)} onConfirm={() => void start(asking)}
          title={`Start the ${AGENT_NAMES[asking]}?`} confirmLabel="Start"
          body={asking === "classify"
            ? `This sends ${count(toSend)} distinct values to ${data.models.classification}. It stops at ${count(data.token_budget)} tokens. Every proposal is listed under Review.`
            : `This profiles bronze and makes up to three calls per column to ${data.models.rule_proposal}, which can take a few minutes. It stops at ${count(data.token_budget)} tokens. Every proposal is listed under Review.`} />
      )}
    </>
  );
}

function StartButton({ label, disabled, onClick }: { label: string; disabled: boolean; onClick: () => void }) {
  return <button className="button secondary small" disabled={disabled} onClick={onClick}><Play size={13} />{label}</button>;
}

type Numbers = Record<string, unknown>;
const CLASSIFY_COLUMNS = [["sent", "Sent"], ["held_below_min_rows", "Held, too rare"], ["failed_validation", "Failed validation"], ["held_on_budget", "Held on budget"],
  ["provisional", "Applied provisionally"], ["proposed", "Held for review"], ["calls", "Calls"], ["tokens_in", "Tokens in"], ["tokens_out", "Tokens out"]];

function JobPanel({ job, onReview }: { job: AgentJob; onReview: () => void }) {
  const name = job.agent ? AGENT_NAMES[job.agent] : "Agent";
  if (job.status === "running") {
    return <Notice><LoaderCircle size={15} className="spin" style={{ verticalAlign: "-2px", marginRight: 6 }} /><strong>{name} is running</strong>, started {dateLabel(job.started_at)}. This page updates when it finishes.</Notice>;
  }
  if (job.status === "failed") {
    return <Notice tone="danger"><strong>{name} stopped with an error.</strong> {job.error} Anything it stored before stopping is under Review.</Notice>;
  }
  const summary = job.summary ?? {};
  const dq: Numbers = summary.dq ?? {};
  const number = (value: unknown) => count(Number(value ?? 0));
  return (
    <Panel title={`${name}: last run`} description={`Finished ${dateLabel(job.finished_at)}`}
      action={<button className="button secondary small" onClick={onReview}>Open review<ArrowRight size={14} /></button>}>
      {job.agent === "classify" ? (
        <DataTable rows={Object.entries(summary)} rowKey={([task]) => task} minWidth={860} columns={[
          { key: "task", label: "Values", render: ([task]) => <strong>{TASK_NAMES[task] ?? task}</strong> },
          ...CLASSIFY_COLUMNS.map(([key, label]): Column<[string, Numbers]> => ({ key, label, align: "right", render: ([, numbers]) => number(numbers[key]) })),
        ]} />
      ) : (
        <div className="panel-body">
          <dl className="key-value-list">
            <div><dt>Proposals stored</dt><dd>{number(dq.proposals)}</dd></div>
            {Object.entries(dq.by_validation as Numbers ?? {}).map(([validation, rows]) => <div key={validation}><dt><StatusBadge status={validation} /></dt><dd>{number(rows)}</dd></div>)}
            <div><dt>Probe queries</dt><dd>{number(dq.probes)} asked for, {number(dq.probes_rejected)} refused</dd></div>
            <div><dt>Model calls</dt><dd>{number(dq.calls)}</dd></div>
            <div><dt>Tokens</dt><dd>{number(dq.tokens_in)} in, {number(dq.tokens_out)} out</dd></div>
            {dq.stopped_on_budget === true && <div><dt>Stopped early</dt><dd>Token budget reached</dd></div>}
          </dl>
        </div>
      )}
    </Panel>
  );
}
