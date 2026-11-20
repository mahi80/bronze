"use client";

// The three stages side by side with what went in and what came out, so a reader can see the pipeline, not just totals.
import { ArrowDown, ArrowRight, CheckCheck } from "lucide-react";
import type { Overview, Quality, RunDetail } from "../api";
import { count, duration, percent, useApi, words } from "../api";
import type { Navigate } from "../console";
import { StartPanel } from "../start";
import { LayerDot, Loading, Notice } from "../ui";
import type { Pipeline } from "./runs";
import { RunButton } from "./runs";

export function PipelineView({ overview, refresh, pipeline, onChanged, onNavigate }: {
  overview: { data?: Overview; loading: boolean }; refresh: number; pipeline: Pipeline; onChanged: () => void; onNavigate: Navigate;
}) {
  const last = overview.data?.last_run;
  const detail = useApi<RunDetail>(last ? `runs/${last.run_id}` : null, refresh);
  const quality = useApi<Quality>(overview.data?.accounting ? "quality" : null, refresh);
  const heading = (
    <div className="page-heading">
      <div>
        <h1>Pipeline</h1>
        <p>Each stage on its own: what went in, what came out, and the line that proves nothing was lost between them.</p>
      </div>
      {overview.data?.accounting && <div className="heading-actions"><RunButton pipeline={pipeline} /></div>}
    </div>
  );
  const data = overview.data;
  if (!data) return <>{heading}{overview.loading && <Loading />}</>;
  const rows = data.accounting;
  if (!rows) return <>{heading}<StartPanel pipeline={pipeline} onChanged={onChanged} /></>;

  const stage = (name: string) => detail.data?.stages.find((item) => item.stage === name);
  const bronze = stage("bronze");
  const silver = stage("silver");
  const goldStage = stage("gold");
  const files = data.files ?? [];
  const models = data.gold ?? [];
  const goldRows = models.reduce((total, model) => total + model.rows, 0);
  const current = rows.silver - rows.duplicates;
  const reasons = (quality.data?.reject_reasons ?? []).slice(0, 4);
  const strengths = quality.data?.duplicates ?? [];
  const unmapped = data.signals?.find((signal) => signal.signal === "unmapped_rows_pct");

  return (
    <>
      {heading}
      {detail.error && <Notice tone="danger">{detail.error}</Notice>}
      <div className="pipeline-run">Run {rows.run_id}{last?.rule_set_version && <> · rule set <code>{last.rule_set_version}</code></>}{last && <> · took {duration(last.duration_ms)}</>}</div>

      <article className="stage-card bronze">
        <header className="stage-head">
          <span className="stage-step">1</span>
          <div><h2><LayerDot layer="bronze" />Bronze</h2><p>Every delivered file lands here exactly as received.</p></div>
          {bronze && <span className="stage-time">{duration(bronze.duration_ms)}</span>}
        </header>
        <div className="stage-columns">
          <section>
            <h3>In · files delivered</h3>
            <ul className="stage-list">
              {files.map((file) => (
                <li key={file.source_file}>
                  <strong>{count(file.row_count)}</strong>
                  <span>{file.source_file}<small>sha256 {file.sha256}…</small></span>
                </li>
              ))}
            </ul>
          </section>
          <section>
            <h3>Out · bronze.tickets_raw</h3>
            <ul className="stage-list">
              <li><strong>{count(rows.bronze)}</strong><span>rows stored, every column kept as text</span></li>
              {bronze && <li><strong>{count(bronze.rows_out)}</strong><span>new rows this run<small>{count((bronze.rows_in ?? 0) - (bronze.rows_out ?? 0))} already present, skipped by file hash</small></span></li>}
            </ul>
          </section>
        </div>
        <footer className="stage-foot">
          <span><CheckCheck size={15} />A re-delivered file is recognised by its content hash and not loaded twice.</span>
          <button className="text-link" onClick={() => onNavigate("data")}>Inspect bronze rows<ArrowRight size={14} /></button>
        </footer>
      </article>

      <div className="stage-arrow"><ArrowDown size={18} /></div>

      <article className="stage-card silver">
        <header className="stage-head">
          <span className="stage-step">2</span>
          <div><h2><LayerDot layer="silver" />Silver</h2><p>Clean, validate and flag, without dropping a row silently.</p></div>
          {silver && <span className="stage-time">{duration(silver.duration_ms)}</span>}
        </header>
        <div className="stage-columns">
          <section>
            <h3>In · bronze rows</h3>
            <ul className="stage-list">
              <li><strong>{count(rows.bronze)}</strong><span>rows read from bronze<small>the full rebuild reads every row on every run</small></span></li>
            </ul>
            {reasons.length > 0 && (
              <>
                <div className="stage-chips">
                  {reasons.map((reason) => <span key={reason.reason} className="tag">{words(reason.reason)} · {count(reason.rows)}</span>)}
                </div>
                <p className="stage-note">Each chip counts rows failing that check; one row can fail several, so the chips add up to more than the {count(rows.rejected)} quarantined.</p>
              </>
            )}
          </section>
          <section>
            <h3>Out · silver</h3>
            <ul className="stage-list">
              <li><strong>{count(rows.silver)}</strong><span>valid tickets<span className="muted"> · {count(rows.rejected)} quarantined, {count(rows.superseded)} superseded</span></span></li>
              <li><strong>{count(rows.duplicates)}</strong><span>duplicates flagged, not deleted{strengths.length > 0 && <small>{strengths.map((item) => `${words(item.strength)} ${count(item.rows)}`).join(" · ")}</small>}</span></li>
              <li><strong>{count(current)}</strong><span>current tickets, the only rows gold reads</span></li>
            </ul>
          </section>
        </div>
        <footer className="stage-foot">
          <span><CheckCheck size={15} /><code>{count(rows.bronze)} = {count(rows.silver)} + {count(rows.rejected)} + {count(rows.superseded)}</code></span>
          <span className="muted">Unmapped {unmapped ? percent(unmapped.value, unmapped.value === 0 ? 0 : 2) : "—"}</span>
          <button className="text-link" onClick={() => onNavigate("quality")}>Rule effects and rejects<ArrowRight size={14} /></button>
        </footer>
      </article>

      <div className="stage-arrow"><ArrowDown size={18} /></div>

      <article className="stage-card gold">
        <header className="stage-head">
          <span className="stage-step">3</span>
          <div><h2><LayerDot layer="gold" />Gold</h2><p>Group-by models, rebuilt from current tickets on every run.</p></div>
          {goldStage && <span className="stage-time">{duration(goldStage.duration_ms)}</span>}
        </header>
        <div className="stage-columns">
          <section>
            <h3>In · current tickets</h3>
            <ul className="stage-list">
              <li><strong>{count(goldStage?.rows_in ?? current)}</strong><span>current tickets<small>duplicates are excluded; each ticket counts once per model</small></span></li>
            </ul>
          </section>
          <section>
            <h3>Out · {models.length} models</h3>
            <ul className="stage-list">
              {models.map((model) => (
                <li key={model.model}><strong>{count(model.rows)}</strong><span>{words(model.model)}<small>group-by rows</small></span></li>
              ))}
              <li><strong>{count(goldRows)}</strong><span>rows in total</span></li>
            </ul>
          </section>
        </div>
        <footer className="stage-foot">
          <span><CheckCheck size={15} />Gold row counts are group counts: a new ticket adds a row only where its group key is new.</span>
          <button className="text-link" onClick={() => onNavigate("gold")}>Open gold tables<ArrowRight size={14} /></button>
        </footer>
      </article>
    </>
  );
}
