"use client";

// Small building blocks shared by every screen. Brand, StatusBadge, Notice, EmptyState and Loading come from my
// DataMigration console; Panel, Meter, DataTable and ConfirmDialog were added for this one.
import { AlertCircle, ArrowDown, ArrowUp, Layers3, LoaderCircle, ShieldCheck } from "lucide-react";
import type { LucideIcon } from "lucide-react";
import { useEffect, useMemo, useRef, useState } from "react";
import type { ReactNode } from "react";

export function Brand() {
  return (
    <div className="brand">
      <span className="brand-symbol"><Layers3 size={22} strokeWidth={2.1} /></span>
      <span>Medallion<small>Ticket pipeline console</small></span>
    </div>
  );
}

type Tone = "success" | "warning" | "info" | "danger" | "neutral";

const STATUS: Record<string, [Tone, string]> = {
  succeeded: ["success", "Succeeded"], failed: ["danger", "Failed"], running: ["info", "Running"],
  finished: ["success", "Finished"], idle: ["neutral", "Not running"],
  ok: ["success", "Within limits"], warn: ["warning", "Warning"], fail: ["danger", "Over the limit"],
  proposed: ["warning", "Held for review"], provisional: ["info", "Applied, awaiting review"],
  validated: ["success", "Row count confirmed"], rejected_count_mismatch: ["danger", "Row count mismatch"],
  rejected_unsafe_sql: ["danger", "Unsafe SQL blocked"], rejected_sql_error: ["danger", "SQL error"],
};

/** A status with a word beside the colour, so the colour never carries the meaning alone. */
export function StatusBadge({ status }: { status: string }) {
  const [tone, label] = STATUS[status] ?? ["neutral", status.replaceAll("_", " ")];
  return <span className={`status-badge ${tone}`}><span className="status-dot" />{label}</span>;
}

export function Notice({ children, tone = "info" }: { children: ReactNode; tone?: "info" | "danger" | "success" | "warning" }) {
  const Icon = tone === "success" ? ShieldCheck : AlertCircle;
  return (
    <div className={`notice ${tone}`} role={tone === "danger" ? "alert" : "status"}>
      <Icon size={18} aria-hidden="true" />
      <div>{children}</div>
    </div>
  );
}

export function EmptyState({ icon: Icon, title, description, action }: { icon: LucideIcon; title: string; description: string; action?: ReactNode }) {
  return (
    <div className="empty-state">
      <span className="empty-icon"><Icon size={26} /></span>
      <h3>{title}</h3>
      <p>{description}</p>
      {action}
    </div>
  );
}

export function Loading({ label = "Loading" }: { label?: string }) {
  return <div className="loading-state" role="status"><LoaderCircle size={22} className="spin" /><span>{label}…</span></div>;
}

export function Panel({ title, description, count: badge, action, children }: { title: string; description?: string; count?: number; action?: ReactNode; children: ReactNode }) {
  return (
    <section className="panel">
      <div className="panel-heading">
        <div>
          <h2>{title}{badge !== undefined && <span className={`count-label ${badge ? "amber" : ""}`}>{badge}</span>}</h2>
          {description && <p>{description}</p>}
        </div>
        {action}
      </div>
      {children}
    </section>
  );
}

/** A share of a whole, or a value against its limit. The track is a lighter step of the fill colour. */
export function Meter({ share, tone = "teal", label }: { share: number; tone?: "teal" | "amber" | "red"; label: string }) {
  const width = Math.max(0, Math.min(1, share)) * 100;
  return <span className={`meter ${tone}`} role="img" aria-label={label}><span style={{ width: `${width}%` }} /></span>;
}

export function LayerDot({ layer }: { layer: "bronze" | "silver" | "current" | "gold" }) {
  return <span className={`layer-dot ${layer}`} aria-hidden="true" />;
}

/** A value exactly as it arrived in the file. An empty field is shown as such, not as a gap. */
export function Raw({ value }: { value: unknown }) {
  if (value === null || value === undefined) return <span className="muted">—</span>;
  const text = String(value);
  return text === "" ? <span className="raw-value blank">empty</span> : <span className="raw-value">{text}</span>;
}

export interface Column<Row> {
  key: string;
  label: ReactNode;
  align?: "right";
  render: (row: Row) => ReactNode;
  /** Give a value to make the column sortable by clicking its header. */
  sortBy?: (row: Row) => string | number | null;
}

/** `stack` turns each row into a labelled block on a phone, for tables whose last column holds the actions or the point. */
export function DataTable<Row>({ columns, rows, rowKey, minWidth, pageSize, stack }: {
  columns: Column<Row>[]; rows: Row[]; rowKey: (row: Row) => string | number; minWidth?: number; pageSize?: number; stack?: boolean;
}) {
  const [sort, setSort] = useState<{ key: string; descending: boolean } | null>(null);
  const [page, setPage] = useState(0);
  const sorted = useMemo(() => {
    const sortBy = sort && columns.find((column) => column.key === sort.key)?.sortBy;
    if (!sort || !sortBy) return rows;
    const direction = sort.descending ? -1 : 1;
    return [...rows].sort((a, b) => {
      const [left, right] = [sortBy(a), sortBy(b)];
      if (left === right) return 0;
      if (left === null) return 1;            // empty values last, whichever way the column is sorted
      if (right === null) return -1;
      return (left < right ? -1 : 1) * direction;
    });
  }, [rows, sort, columns]);

  function toggle(key: string) {
    setSort((current) => current?.key !== key ? { key, descending: true } : current.descending ? { key, descending: false } : null);
    setPage(0);
  }

  const size = pageSize ?? Math.max(1, sorted.length);
  const lastPage = Math.max(0, Math.ceil(sorted.length / size) - 1);
  const current = Math.min(page, lastPage);                  // a filter can shrink the rows under the page being shown
  const visible = sorted.slice(current * size, (current + 1) * size);

  return (
    <>
      <div className="table-scroll">
        <table className={`data-table ${stack ? "stack" : ""}`} style={minWidth ? { minWidth } : undefined}>
          <thead>
            <tr>
              {columns.map((column) => (
                <th key={column.key} className={column.align} aria-sort={sort?.key === column.key ? (sort.descending ? "descending" : "ascending") : undefined}>
                  {column.sortBy ? (
                    <button className="sort-button" onClick={() => toggle(column.key)}>
                      {column.label}
                      {sort?.key === column.key && (sort.descending ? <ArrowDown size={12} /> : <ArrowUp size={12} />)}
                    </button>
                  ) : column.label}
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {visible.map((row) => (
              <tr key={rowKey(row)}>
                {columns.map((column) => (
                  <td key={column.key} className={column.align} data-label={typeof column.label === "string" ? column.label : undefined}>{column.render(row)}</td>
                ))}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {lastPage > 0 && (
        <div className="table-footer pager">
          <span>Rows {current * size + 1} to {Math.min(sorted.length, (current + 1) * size)} of {sorted.length.toLocaleString("en-US")}</span>
          <button className="button secondary small" disabled={current === 0} onClick={() => setPage(current - 1)}>Previous</button>
          <button className="button secondary small" disabled={current === lastPage} onClick={() => setPage(current + 1)}>Next</button>
        </div>
      )}
    </>
  );
}

export function Tabs({ tabs, current, onChange, label }: { tabs: { id: string; label: string }[]; current: string; onChange: (id: string) => void; label: string }) {
  return (
    <div className="tabs" role="tablist" aria-label={label}>
      {tabs.map((tab) => <button key={tab.id} role="tab" aria-selected={current === tab.id} onClick={() => onChange(tab.id)}>{tab.label}</button>)}
    </div>
  );
}

/** Approvals, rejections and agent runs go through this dialog: it names the action and lists exactly what it covers. */
export function ConfirmDialog({ title, body, items, confirmLabel, danger, busy, onCancel, onConfirm }: {
  title: string; body: string; items?: string[]; confirmLabel: string; danger?: boolean; busy: boolean; onCancel: () => void; onConfirm: () => void;
}) {
  const dialog = useRef<HTMLDialogElement>(null);
  useEffect(() => { dialog.current?.showModal(); }, []);
  return (
    <dialog ref={dialog} className="confirm-dialog" aria-labelledby="confirm-title" onCancel={(event) => { event.preventDefault(); if (!busy) onCancel(); }}>
      <div className="dialog-icon"><ShieldCheck size={24} /></div>
      <h2 id="confirm-title">{title}</h2>
      <p>{body}</p>
      {items && items.length > 0 && <div className="dialog-items">{items.map((item) => <code key={item}>{item}</code>)}</div>}
      <div className="dialog-actions">
        <button className="button secondary" onClick={onCancel} disabled={busy}>Cancel</button>
        <button className={`button ${danger ? "danger" : "primary"}`} onClick={onConfirm} disabled={busy}>
          {busy && <LoaderCircle size={16} className="spin" />}
          {busy ? "Working…" : confirmLabel}
        </button>
      </div>
    </dialog>
  );
}
