"use client";

// The approval gate. Proposals are listed with their evidence; a decision is sent for exactly the rows on screen
// and recorded under the reviewer's name. The pattern follows the plan approval in my DataMigration console.
import { ArrowRight, Check, ClipboardCheck, ShieldCheck, X } from "lucide-react";
import { useState } from "react";
import type { MappingProposal, ReviewQueue, RuleProposal } from "../api";
import { api, count, dateLabel, errorMessage, useApi, words } from "../api";
import type { Navigate } from "../console";
import { ConfirmDialog, DataTable, EmptyState, Loading, Meter, Notice, Panel, StatusBadge } from "../ui";

const TASKS: Record<string, { title: string; from: string; to: string }> = {
  category: { title: "Category mappings", from: "Spelling in the file", to: "Standard category" },
  priority: { title: "Priority mappings", from: "Value in the file", to: "Standard priority" },
  person: { title: "Submitter merges", from: "Name in the file", to: "Person" },
  issue: { title: "Issue type and hazard signal", from: "Description template", to: "Issue type / hazard signal" },
};

type Verdict = "approve" | "reject";
type Decision =
  | { kind: "mappings"; verdict: Verdict; items: MappingProposal[] }
  | { kind: "rules"; verdict: Verdict; items: RuleProposal[] };

type Decidable = Pick<MappingProposal, "task" | "key" | "value">;

const idOf = (item: { task: string; key: string }) => `${item.task}|${item.key}`;
const plural = (n: number, word: string) => `${count(n)} ${word}${n === 1 ? "" : "s"}`;

export function ReviewView({ refresh, onChanged, onNavigate }: { refresh: number; onChanged: () => void; onNavigate: Navigate }) {
  const queue = useApi<ReviewQueue>("review", refresh);
  const [selected, setSelected] = useState<Set<string>>(new Set());
  const [corrections, setCorrections] = useState<Record<string, string>>({});      // reviewer's own category, by mapping id
  const [asking, setAsking] = useState<Decision | null>(null);
  const [busy, setBusy] = useState(false);
  const [outcome, setOutcome] = useState<{ tone: "success" | "danger"; text: string } | null>(null);

  const heading = (
    <div className="page-heading">
      <div>
        <h1>Review</h1>
        <p>Agents write proposals and a person decides. A recurring category the rule set has not seen is queued here by the run itself, with no proposed value. Confident category and issue mappings are applied provisionally until reviewed; everything else is held. Either way, silver changes only on the next run.</p>
      </div>
    </div>
  );
  if (!queue.data) return <>{heading}{queue.error ? <Notice tone="danger">{queue.error}</Notice> : <Loading />}</>;
  const { mappings, rules, rejected, rule_set: ruleSet, categories, approver } = queue.data;

  /** The category this mapping would be approved with: the reviewer's choice, else the agent's if it is a standard one. */
  const categoryFor = (item: Decidable) => corrections[idOf(item)] ?? (categories.includes(item.value) ? item.value : "");
  const canApprove = (item: Decidable) => item.task !== "category" || categoryFor(item) !== "";

  async function submit(decision: Decision) {
    setBusy(true);
    try {
      let changed = 0;
      if (decision.kind === "rules") {
        changed = (await api<{ changed: number }>("review/rules", { ids: decision.items.map((rule) => rule.id), decision: decision.verdict })).changed;
      } else {
        // one request per task and corrected value, each carrying the exact keys shown on screen
        const groups = new Map<string, { task: string; keys: string[]; set_value?: string }>();
        for (const item of decision.items) {
          const corrected = decision.verdict === "approve" && item.task === "category" && categoryFor(item) !== item.value ? categoryFor(item) : undefined;
          const group = groups.get(`${item.task}|${corrected ?? ""}`) ?? { task: item.task, keys: [], set_value: corrected };
          group.keys.push(item.key);
          groups.set(`${item.task}|${corrected ?? ""}`, group);
        }
        for (const group of groups.values()) {
          changed += (await api<{ changed: number }>("review/mappings", { ...group, decision: decision.verdict })).changed;
        }
      }
      const what = plural(changed, decision.kind === "rules" ? "rule proposal" : "mapping");
      const then = decision.verdict === "reject" ? " Those values stay as delivered and counted as unmapped until a mapping is approved; the Rejected list below can reopen the decision."
        : decision.kind === "rules" ? " It runs as a check on every load from the next run. Only proposals with a confirmed row count can be approved."
        : " Silver is rebuilt with it on the next run.";
      setOutcome({ tone: "success", text: `${decision.verdict === "approve" ? "Approved" : "Rejected"} ${what} as ${approver}.${then}` });
      setSelected(new Set());
    } catch (failure) {
      setOutcome({ tone: "danger", text: `${errorMessage(failure)} The lists below show what was recorded.` });
    } finally {
      setBusy(false);
      setAsking(null);
      onChanged();
    }
  }

  function toggle(ids: string[], on: boolean) {
    setSelected((current) => {
      const next = new Set(current);
      for (const id of ids) { if (on) next.add(id); else next.delete(id); }
      return next;
    });
  }

  const tasks = Object.keys(TASKS).filter((task) => mappings.some((item) => item.task === task));
  return (
    <>
      {heading}
      {queue.error && <Notice tone="danger">{queue.error}</Notice>}
      {outcome && <Notice tone={outcome.tone}>{outcome.text}</Notice>}

      {mappings.length + rules.length === 0 ? (
        <section className="panel">
          <EmptyState icon={ClipboardCheck} title="Nothing is waiting for a decision"
            description="When an agent proposes a mapping or a rule, or a run meets a recurring category with no mapping, it is listed here with its evidence. The pipeline never waits for this page: unmapped values are loaded, flagged and counted."
            action={<button className="button secondary" onClick={() => onNavigate("agents")}>Go to agents<ArrowRight size={15} /></button>} />
        </section>
      ) : (
        <div className="approval-panel">
          <span className="approval-icon"><ShieldCheck size={23} /></span>
          <div>
            <h2>{plural(mappings.length + rules.length, "proposal")} waiting for a decision</h2>
            <p>Your decisions are recorded as <strong>{approver}</strong>. Check the value, the confidence and the reason before approving.</p>
          </div>
        </div>
      )}

      {tasks.map((task) => {
        const items = mappings.filter((item) => item.task === task);
        const chosen = items.filter((item) => selected.has(idOf(item)));
        const everySelected = chosen.length === items.length;
        return (
          <Panel key={task} title={TASKS[task].title} count={items.length}>
            <div className="bulk-bar">
              <span>{chosen.length ? `${chosen.length} selected` : "Select rows to decide several at once."}</span>
              <button className="button secondary small" disabled={!chosen.length} onClick={() => setAsking({ kind: "mappings", verdict: "reject", items: chosen })}>Reject selected</button>
              <button className="button primary small" disabled={!chosen.length || !chosen.every(canApprove)} onClick={() => setAsking({ kind: "mappings", verdict: "approve", items: chosen })}>Approve selected</button>
            </div>
            <DataTable stack rows={items} rowKey={idOf} minWidth={860} columns={[
              { key: "select", label: <input type="checkbox" aria-label={`Select all ${TASKS[task].title.toLowerCase()}`} checked={everySelected} onChange={(event) => toggle(items.map(idOf), event.target.checked)} />,
                render: (item) => <input type="checkbox" aria-label={`Select ${item.key}`} checked={selected.has(idOf(item))} onChange={(event) => toggle([idOf(item)], event.target.checked)} /> },
              { key: "from", label: TASKS[task].from, render: (item) => <code>{item.key}</code> },
              { key: "to", label: TASKS[task].to, render: (item) => task !== "category" ? <strong>{item.value}</strong> : (
                <>
                  <select aria-label={`Category for ${item.key}`} value={categoryFor(item)} onChange={(event) => setCorrections((current) => ({ ...current, [idOf(item)]: event.target.value }))}>
                    {!categoryFor(item) && <option value="">Choose a category</option>}
                    {categories.map((category) => <option key={category} value={category}>{category}</option>)}
                  </select>
                  {categoryFor(item) !== item.value && <span className="table-subtitle">{item.model ? `Agent proposed ${item.value}` : "No proposal: queued by the run"}</span>}
                </>
              ) },
              { key: "confidence", label: "Confidence", align: "right", sortBy: (item) => item.confidence, render: (item) => item.confidence === null ? "—" : (
                <span className="meter-cell">{item.confidence.toFixed(2)}<Meter share={item.confidence} tone={item.status === "proposed" ? "amber" : "teal"} label={`Confidence ${item.confidence}`} /></span>
              ) },
              { key: "status", label: "Status", render: (item) => <StatusBadge status={item.status} /> },
              { key: "reason", label: "Reason", render: (item) => (
                <>
                  {item.reason || "—"}{item.model && <span className="table-subtitle">{item.model}</span>}
                  <span className="table-subtitle">If rejected, {plural(item.affected, "ticket")} {item.affected === 1 ? "stays" : "stay"} counted as unmapped.</span>
                </>
              ) },
              { key: "actions", label: <span className="sr-only">Decide</span>, render: (item) => (
                <span className="row-actions">
                  <button className="button secondary small" onClick={() => setAsking({ kind: "mappings", verdict: "reject", items: [item] })}><X size={14} />Reject</button>
                  <button className="button primary small" disabled={!canApprove(item)} onClick={() => setAsking({ kind: "mappings", verdict: "approve", items: [item] })}><Check size={14} />Approve</button>
                </span>
              ) },
            ]} />
          </Panel>
        );
      })}

      {rules.length > 0 && (
        <Panel title="Data quality rules" count={rules.length} description="Each proposal's SQL is checked for safety, then run read-only against bronze. The measured row count must match what the agent claimed.">
          {rules.map((rule) => (
            <article className="proposal" key={rule.id}>
              <div className="proposal-head">
                <h3><code>{rule.rule_id}</code><StatusBadge status={rule.validation} /></h3>
                <span className="row-actions">
                  <button className="button secondary small" onClick={() => setAsking({ kind: "rules", verdict: "reject", items: [rule] })}><X size={14} />Reject</button>
                  <button className="button primary small" disabled={rule.validation !== "validated"} title={rule.validation === "validated" ? undefined : "Only a proposal with a confirmed row count can be approved"}
                    onClick={() => setAsking({ kind: "rules", verdict: "approve", items: [rule] })}><Check size={14} />Approve as a check</button>
                </span>
              </div>
              <p>{rule.finding}</p>
              <p className="why">{rule.why_it_matters}</p>
              <div className="proposal-facts">
                <span className="tag">{words(rule.action)}</span>
                <span className={`tag ${rule.risk === "high" ? "warning" : ""}`}>{words(rule.risk)} risk</span>
                <span>Columns <strong>{rule.column_names.join(", ")}</strong></span>
                <span>Agent expected <strong>{plural(rule.expected_rows ?? 0, "row")}</strong></span>
                <span>{rule.measured_rows === null ? "Not measured: the SQL did not run" : <>Database measured <strong>{count(rule.measured_rows)}</strong></>}</span>
              </div>
              {rule.validation_msg && <div className="proposal-facts"><span>{rule.validation_msg}</span></div>}
              <pre className="code-block">{rule.sql}</pre>
            </article>
          ))}
        </Panel>
      )}

      {rejected.length > 0 && (
        <Panel title="Rejected, not applied" count={rejected.length}
          description="Values a person turned down. Silver keeps the raw value for these rows and the Unmapped rows check counts them; approving one here reopens the decision.">
          <DataTable stack rows={rejected} rowKey={idOf} minWidth={900} columns={[
            { key: "task", label: "Mapping table", render: (row) => <strong>{TASKS[row.task]?.title ?? row.task}</strong> },
            { key: "from", label: "Value in the file", render: (row) => <code>{row.key}</code> },
            { key: "to", label: "Approve as", render: (row) => row.task !== "category" ? <strong>{row.value}</strong> : (
              <>
                <select aria-label={`Category to reopen ${row.key}`} value={categoryFor(row)} onChange={(event) => setCorrections((current) => ({ ...current, [idOf(row)]: event.target.value }))}>
                  {!categoryFor(row) && <option value="">Choose a category</option>}
                  {categories.map((category) => <option key={category} value={category}>{category}</option>)}
                </select>
                <span className="table-subtitle">Rejected value {row.value} · approving maps {plural(row.affected, "ticket")} on the next run</span>
              </>
            ) },
            { key: "who", label: "Rejected by", render: (row) => <>{row.decided_by || "—"}<span className="table-subtitle">{dateLabel(row.decided_at)}</span></> },
            { key: "actions", label: <span className="sr-only">Reopen</span>, render: (row) => (
              <button className="button secondary small" disabled={!canApprove(row)}
                onClick={() => setAsking({ kind: "mappings", verdict: "approve", items: [{ ...row, status: "rejected", confidence: null, reason: null, model: null }] })}>
                <Check size={14} />Approve instead
              </button>
            ) },
          ]} />
        </Panel>
      )}

      <Panel title="The approved rule set" description="What silver is built with. Seed mappings were labelled by hand; the other columns are agent proposals at each stage of review.">
        <DataTable rows={ruleSet} rowKey={(row) => row.task} minWidth={640} columns={[
          { key: "task", label: "Mapping table", render: (row) => <strong>{TASKS[row.task]?.title ?? row.task}</strong> },
          { key: "approved", label: "Approved", align: "right", render: (row) => count(row.approved) },
          { key: "reviewed", label: "Of which from review", align: "right", render: (row) => count(row.approved_after_review) },
          { key: "provisional", label: "Applied, awaiting review", align: "right", render: (row) => count(row.provisional) },
          { key: "proposed", label: "Held", align: "right", render: (row) => count(row.proposed) },
          { key: "rejected", label: "Rejected", align: "right", render: (row) => count(row.rejected) },
        ]} />
        <div className="table-footer">
          Rule set version <code>{queue.data.rule_set_version}</code>. {queue.data.approved_checks
            ? `${plural(queue.data.approved_checks, "approved agent check")} ${queue.data.approved_checks === 1 ? "runs" : "run"} on every load.`
            : "No agent check has been approved yet."}
        </div>
      </Panel>

      {asking && <ConfirmDialog busy={busy} danger={asking.verdict === "reject"} onCancel={() => setAsking(null)} onConfirm={() => void submit(asking)} {...dialogText(asking, approver, categoryFor)} />}
    </>
  );
}

function dialogText(decision: Decision, approver: string, categoryFor: (item: Decidable) => string) {
  const approving = decision.verdict === "approve";
  if (decision.kind === "rules") {
    return {
      title: `${approving ? "Approve" : "Reject"} ${plural(decision.items.length, "rule proposal")}?`,
      body: approving
        ? `Recorded as ${approver}. The predicate then runs read-only on every load and its row count is reported with the documented rules. It does not rewrite silver.`
        : `Recorded as ${approver}. The proposal is closed and will not run.`,
      items: decision.items.map((rule) => rule.rule_id),
      confirmLabel: approving ? "Approve as a check" : "Reject",
    };
  }
  const affected = decision.items.reduce((total, item) => total + item.affected, 0);
  return {
    title: `${approving ? "Approve" : "Reject"} ${plural(decision.items.length, "mapping")}?`,
    body: approving
      ? `Recorded as ${approver}. Approved mappings join the rule set, and ${count(affected)} ${affected === 1 ? "ticket uses" : "tickets use"} it on the next run.`
      : `Recorded as ${approver}. The value is not applied: ${count(affected)} ${affected === 1 ? "ticket stays" : "tickets stay"} as delivered and counted as unmapped, and the Unmapped rows check shows it until a mapping is approved. The Rejected list can reopen this decision.`,
    items: decision.items.map((item) => `${item.key}  →  ${approving && item.task === "category" ? categoryFor(item) : item.value}`),
    confirmLabel: approving ? "Approve" : "Reject",
  };
}
