"use client";

// The workspace shell: navigation, the "Run pipeline" action and the notices every screen shares.
// The layout follows the shell of my DataMigration console.
import { Bot, ChevronRight, ClipboardCheck, Database, LayoutDashboard, Menu, Route, ShieldCheck, Table2, Workflow, X } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useCallback, useEffect, useState } from "react";
import type { Overview, RunResult } from "./api";
import { api, errorMessage, useApi } from "./api";
import { Brand, EmptyState, Loading, Notice } from "./ui";
import { AgentsView } from "./views/agents";
import { DataView } from "./views/data";
import { GoldView } from "./views/gold";
import { OverviewView } from "./views/overview";
import { QualityView } from "./views/quality";
import { ReviewView } from "./views/review";
import { RunButton, RunsView } from "./views/runs";
import { TraceView } from "./views/trace";
import { UploadButton } from "./upload";

const VIEWS = {
  overview: { label: "Overview", icon: LayoutDashboard },
  runs: { label: "Runs", icon: Workflow },
  data: { label: "Data", icon: Database },
  review: { label: "Review", icon: ClipboardCheck },
  quality: { label: "Data quality", icon: ShieldCheck },
  gold: { label: "Gold", icon: Table2 },
  trace: { label: "Ticket trace", icon: Route },
  agents: { label: "Agents", icon: Bot },
} satisfies Record<string, { label: string; icon: LucideIcon }>;

export type View = keyof typeof VIEWS;
export interface Place { view: View; run: number | null; ticket: string | null }
export type Navigate = (view: View, detail?: { run?: number; ticket?: string }) => void;

function readPlace(): Place {
  const params = new URLSearchParams(window.location.search);
  const view = params.get("view") || "overview";
  const run = Number(params.get("run"));
  return { view: view in VIEWS ? view as View : "overview", run: Number.isInteger(run) && run > 0 ? run : null, ticket: params.get("ticket") };
}

export function Console() {
  const [place, setPlace] = useState<Place | null>(null);             // null until the browser URL has been read
  const [refresh, setRefresh] = useState(0);                          // bumped after every change, so screens reload
  const [menuOpen, setMenuOpen] = useState(false);
  const [run, setRun] = useState<{ busy: boolean; result?: RunResult; error?: string }>({ busy: false });
  const overview = useApi<Overview>("overview", refresh);

  useEffect(() => {
    const restore = () => { setPlace(readPlace()); setMenuOpen(false); };
    restore();
    window.addEventListener("popstate", restore);
    return () => window.removeEventListener("popstate", restore);
  }, []);

  const navigate: Navigate = useCallback((view, detail = {}) => {
    const query = new URLSearchParams();
    if (view !== "overview") query.set("view", view);
    if (detail.run) query.set("run", String(detail.run));
    if (detail.ticket) query.set("ticket", detail.ticket);
    window.history.pushState({}, "", `${window.location.pathname}${query.size ? `?${query}` : ""}`);
    setPlace({ view, run: detail.run ?? null, ticket: detail.ticket ?? null });
    setMenuOpen(false);
    setRun((current) => current.busy ? current : { busy: false });
    window.scrollTo({ top: 0, behavior: "instant" });
  }, []);

  const changed = useCallback(() => setRefresh((value) => value + 1), []);

  async function runPipeline() {
    setRun({ busy: true });
    try {
      setRun({ busy: false, result: await api<RunResult>("runs", {}) });
    } catch (failure) {
      setRun({ busy: false, error: errorMessage(failure) });
    }
    changed();
  }

  if (!place) return <Loading label="Opening the console" />;

  const data = overview.data;
  const pending = (data?.pending?.mappings ?? 0) + (data?.pending?.rules ?? 0);
  const ruleSetChanged = Boolean(data?.built_with && data.rule_set_version && data.built_with !== data.rule_set_version);
  const pipeline = { run: runPipeline, busy: run.busy };

  return (
    <div className="app-shell">
      <a className="skip-link" href="#main-content">Skip to content</a>
      {menuOpen && <button className="mobile-backdrop" aria-label="Close navigation" onClick={() => setMenuOpen(false)} />}
      <aside className={`sidebar ${menuOpen ? "open" : ""}`}>
        <div className="sidebar-brand">
          <Brand />
          <button className="icon-button mobile-only" aria-label="Close navigation" onClick={() => setMenuOpen(false)}><X size={20} /></button>
        </div>
        <nav aria-label="Main navigation">
          {(Object.keys(VIEWS) as View[]).map((view) => {
            const { label, icon: Icon } = VIEWS[view];
            return (
              <button key={view} className={`nav-item ${place.view === view ? "active" : ""}`} aria-current={place.view === view ? "page" : undefined} onClick={() => navigate(view)}>
                <Icon size={18} /><span>{label}</span>
                {view === "review" && pending > 0 && <small aria-label={`${pending} waiting`}>{pending}</small>}
              </button>
            );
          })}
        </nav>
        <div className="sidebar-guide">
          <span><ShieldCheck size={20} /></span>
          <h3>Agents propose. A person decides.</h3>
          <p>No model runs in the row path. A proposed mapping takes effect on the next run: provisionally if the agent was confident, otherwise once approved.</p>
        </div>
        {data && (
          <div className="sidebar-bottom">
            <div className="profile">
              <span className="profile-avatar">{data.approver.slice(0, 2).toUpperCase()}</span>
              <span className="profile-text"><strong title={data.approver}>{data.approver}</strong><small>Name recorded on approvals</small></span>
            </div>
          </div>
        )}
      </aside>

      <div className="workspace-main">
        <header className="topbar">
          <div className="topbar-left">
            <button className="icon-button mobile-only" aria-label="Open navigation" onClick={() => setMenuOpen(true)}><Menu size={20} /></button>
            <span>Pipeline</span><ChevronRight size={13} /><strong>{VIEWS[place.view].label}</strong>
          </div>
          <div className="topbar-right">
            <span className={`service-state ${overview.error ? "offline" : data ? "online" : ""}`}>
              <span className="status-dot" />{overview.error ? "API not reachable" : data ? "API connected" : "Connecting"}
            </span>
          </div>
        </header>

        <main id="main-content" className="main-content">
          {overview.error && <Notice tone="danger">{overview.error}<button className="text-link" onClick={overview.reload}>Try again</button></Notice>}
          {run.error && <Notice tone="danger">{run.error}</Notice>}
          {run.result && <RunNotice result={run.result} onOpen={() => navigate("runs", { run: run.result?.run_id })} />}
          {ruleSetChanged && !run.busy && (
            <Notice tone="warning">
              The rule set changed after the last run (<code>{data?.built_with}</code> to <code>{data?.rule_set_version}</code>). Silver and gold still show the old one.
              <button className="text-link" onClick={runPipeline}>Run the pipeline</button>
            </Notice>
          )}
          {overview.error ? null       // the API cannot be reached: one message above, instead of one per screen
            : place.view === "overview" ? <OverviewView overview={overview} refresh={refresh} pipeline={pipeline} onNavigate={navigate} onChanged={changed} />
            : data && !data.initialised ? (
              <section className="panel">
                <EmptyState icon={Workflow} title="The pipeline has not run in this database yet"
                  description="Run it once to create the tables and load the files in data/. This screen has nothing to show until then."
                  action={<><RunButton pipeline={pipeline} /><UploadButton onChanged={changed} /></>} />
              </section>
            )
            : place.view === "runs" ? <RunsView runId={place.run} refresh={refresh} pipeline={pipeline} onNavigate={navigate} />
            : place.view === "data" ? <DataView refresh={refresh} />
            : place.view === "review" ? <ReviewView refresh={refresh} onChanged={changed} onNavigate={navigate} />
            : place.view === "quality" ? <QualityView refresh={refresh} onNavigate={navigate} />
            : place.view === "gold" ? <GoldView refresh={refresh} />
            : place.view === "trace" ? <TraceView ticket={place.ticket} refresh={refresh} onNavigate={navigate} />
            : <AgentsView refresh={refresh} onChanged={changed} onNavigate={navigate} />}
        </main>

        <footer className="workspace-footer">
          <span><ShieldCheck size={13} />The source file is read-only. Bronze keeps it exactly as delivered.</span>
          {data?.rule_set_version && <span>Rule set <code>{data.rule_set_version}</code></span>}
        </footer>
      </div>
    </div>
  );
}

function RunNotice({ result, onOpen }: { result: RunResult; onOpen: () => void }) {
  const open = <button className="text-link" onClick={onOpen}>Open run {result.run_id}</button>;
  if (result.status === "failed") {
    return <Notice tone="danger"><strong>Run {result.run_id} failed.</strong> {result.message} The previous silver and gold tables are still in place.{open}</Notice>;
  }
  if (result.checks === "ok") {
    return <Notice tone="success"><strong>Run {result.run_id} succeeded.</strong> Every check is within its limit.{open}</Notice>;
  }
  return (
    <Notice tone="warning">
      <strong>Run {result.run_id} finished, with {result.checks === "fail" ? "a check over its limit" : "a warning"}.</strong> The tables were rebuilt; the alerts say what to look at.{open}
    </Notice>
  );
}
