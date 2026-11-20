"use client";

// What the console shows before anything is loaded: the three stages, and the two ways to begin.
import { ClipboardCheck, Database, ShieldCheck } from "lucide-react";
import type { Pipeline } from "./views/runs";
import { RunButton } from "./views/runs";
import { UploadButton } from "./upload";

export function StartPanel({ pipeline, onChanged }: { pipeline: Pipeline; onChanged: () => void }) {
  return (
    <section className="panel start-panel">
      <div className="start-intro">
        <h2>Nothing has been loaded yet</h2>
        <p>This database is empty. Upload a delivery or load the supplied file, then run the pipeline once. It takes a few seconds, and the same run can be repeated safely.</p>
      </div>
      <ol className="start-steps">
        <li>
          <span className="start-icon"><Database size={19} /></span>
          <div>
            <h3>1 · Land in bronze</h3>
            <p>The supplied <code>data/raw_tickets.csv</code> is copied in row by row: every column kept as text, nothing cleaned on the way in.</p>
          </div>
        </li>
        <li>
          <span className="start-icon"><ShieldCheck size={19} /></span>
          <div>
            <h3>2 · Clean and validate in silver</h3>
            <p>Dates, categories and people are standardised. Invalid rows are quarantined with reasons, duplicates are flagged but kept, and every bronze row is accounted for.</p>
          </div>
        </li>
        <li>
          <span className="start-icon"><ClipboardCheck size={19} /></span>
          <div>
            <h3>3 · Aggregate in gold</h3>
            <p>Three group-by models are rebuilt from the current tickets. New spellings wait in Review for a person; the run itself never waits for one.</p>
          </div>
        </li>
      </ol>
      <div className="start-actions">
        <RunButton pipeline={pipeline} label="Load and run" />
        <UploadButton onChanged={onChanged} />
        <span className="start-hint">Upload a later delivery first and the same run ingests it alongside the supplied file.</span>
      </div>
    </section>
  );
}
