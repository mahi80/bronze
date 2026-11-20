// Shapes returned by pipeline/api.py, the fetch helper that goes through the console's own server, and formatters.
import { useCallback, useEffect, useState } from "react";

export interface Run {
  run_id: number;
  command: string;
  status: string;
  started_at: string;
  finished_at: string | null;
  rule_set_version: string | null;
  message: string | null;
  duration_ms: number | null;
}

export interface Signal {
  signal: string;
  description: string;
  value: number;
  warn: number;
  fail: number;
  level: "ok" | "warn" | "fail";
}

export interface Overview {
  initialised: boolean;
  provider: string;
  approver: string;
  accounting?: { run_id: number; bronze: number; silver: number; rejected: number; superseded: number; duplicates: number } | null;
  last_run?: Run | null;
  rule_set_version?: string;
  built_with?: string | null;
  signals?: Signal[];
  gold?: { model: string; rows: number }[];
  pending?: { mappings: number; rules: number };
  files?: { source_file: string; row_count: number; sha256: string; loaded_at: string }[];
}

export interface RunResult {
  status: "succeeded" | "failed";
  run_id: number;
  checks?: "ok" | "warn" | "fail";
  message?: string;
}

export interface Stage {
  stage: string;
  status: string;
  rows_in: number | null;
  rows_out: number | null;
  rows_rejected: number | null;
  message: string;
  duration_ms: number;
}

export interface RuleEffect {
  rule_id: string;
  title: string;
  rows_checked: number;
  rows_affected: number;
  pct: number | null;
}

export interface RunDetail {
  run: Run;
  stages: Stage[];
  rules: RuleEffect[];
  alerts: { signal: string; level: string; value: number; threshold: number; message: string }[];
  drift: { kind: string; source_file: string | null; detail: unknown; detected_at: string }[];
  lineage: { source_object: string; target_object: string; transformation: string; rule_set_version: string | null; executed_by: string; executed_at: string }[];
}

export interface QualityRule {
  id: string;
  title: string;
  rule: string;
  why: string;
  scope: string;
  origin: "documented" | "agent";
  rows_checked: number | null;
  rows_affected: number | null;
  pct: number | null;
}

export interface Quality {
  run_id: number | null;
  rules: QualityRule[];
  reject_reasons: { reason: string; rows: number }[];
  rejected: { source_row: number; reasons: string[]; raw: Record<string, string> }[];
  duplicates: { strength: string; rows: number }[];
  flags: { flag: string; rows: number }[];
  tags: { column_name: string; semantic_type: string; sensitivity: string; basis: string }[];
}

export interface ColumnProfile {
  column: string;
  rows: number;
  blank: number;
  blank_pct: number;
  distinct: number;
  min_length: number | null;
  max_length: number | null;
  padded_whitespace: number;
  distinct_shapes: number;
  shapes: { shape: string; rows: number; example?: string }[];
  top_values: { value: string; rows: number }[];
  numeric?: { rows: number; min: number; max: number };
  note?: string;
  tags: { semantic_type: string; sensitivity: string; basis: string };
}

export interface DataProfile {
  initialised: boolean;
  table?: string;
  rows?: number;
  purpose?: string;
  columns?: ColumnProfile[];
  cross_column?: {
    candidate_key: string;
    near_duplicates: { identical_except: string[]; groups: number; rows: number }[];
    vocabulary_overlap: { column: string; holds_values_of: string; rows: number; example: string; meaning: string }[];
    timestamp_order: { earlier_column: string; later_column: string; rows_with_both: number; rows_where_later_precedes_earlier: number; pct: number }[];
  };
}

export interface DataRows {
  initialised: boolean;
  columns: string[];
  rows: Record<string, Cell>[];
  total: number;
}

export type Cell = string | number | boolean | null | string[];

export interface GoldTable {
  model: string;
  rows: Record<string, Cell>[];
  total: number;
}

export interface Trace {
  ticket_id: string;
  bronze: Record<string, Cell>[];
  silver: Record<string, Cell> | null;
  copies: { ticket_id: string; dup_match_strength: string }[];
}

export interface MappingProposal {
  task: string;
  key: string;
  value: string;
  status: "proposed" | "provisional";
  confidence: number | null;
  reason: string | null;
  model: string | null;
}

export interface RuleProposal {
  id: number;
  agent_run: string;
  rule_id: string;
  column_names: string[];
  finding: string;
  why_it_matters: string;
  action: string;
  risk: string;
  sql_kind: string;
  sql: string;
  expected_rows: number | null;
  measured_rows: number | null;
  validation: string;
  validation_msg: string | null;
  model: string | null;
  created_at: string;
}

export interface ReviewQueue {
  approver: string;
  mappings: MappingProposal[];
  rules: RuleProposal[];
  rule_set: { task: string; approved: number; approved_after_review: number; provisional: number; proposed: number; rejected: number }[];
  approved_checks: number;
  rule_set_version: string;
  categories: string[];
}

export interface AgentJob {
  status: "idle" | "running" | "finished" | "failed";
  agent?: "classify" | "dq";
  task?: string;
  started_at?: string;
  finished_at?: string;
  summary?: Record<string, Record<string, unknown>>;
  error?: string;
}

export interface Agents {
  provider: string;
  enabled: boolean;
  models: { classification: string | null; rule_proposal: string | null };
  token_budget: number;
  auto_apply_confidence: number;
  min_rows: number;
  unmapped: { task: string; to_send: number; held: number }[];
  calls: { task: string; model: string; calls: number; tokens_in: number; tokens_out: number; mean_latency_ms: number; cost_usd: number | null }[];
  job: AgentJob;
}

export class ApiError extends Error {
  readonly status: number;

  constructor(message: string, status: number) {
    super(message);
    this.name = "ApiError";
    this.status = status;
  }
}

/** Call the pipeline API through the console's server. `body` makes it a POST. */
export async function api<T>(path: string, body?: unknown): Promise<T> {
  const response = await fetch(`/api/backend/${path}`, {
    method: body === undefined ? "GET" : "POST",
    headers: body === undefined ? undefined : { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body),
    cache: "no-store",
  });
  const data: unknown = await response.json().catch(() => null);
  if (!response.ok) {
    const record = data && typeof data === "object" ? data as Record<string, unknown> : {};
    const message = typeof record.detail === "string" ? record.detail
      : Array.isArray(record.detail) ? record.detail.map((item: { msg?: string }) => item.msg || "Invalid input").join(". ")
      : "The request could not be completed. Refresh before trying again.";
    throw new ApiError(message, response.status);
  }
  return data as T;
}

export function errorMessage(error: unknown): string {
  return error instanceof Error ? error.message : "Something went wrong. Please try again.";
}

/** Load one API path, again whenever `refresh` changes. Data stays on screen while it reloads; an answer for a path
 *  that is no longer shown is dropped. */
export function useApi<T>(path: string | null, refresh = 0) {
  const [result, setResult] = useState<{ path: string; data?: T; error?: string } | null>(null);
  const [nonce, setNonce] = useState(0);
  useEffect(() => {
    if (path === null) return;
    let current = true;
    api<T>(path)
      .then((data) => { if (current) setResult({ path, data }); })
      .catch((failure) => {
        if (current) setResult((previous) => ({ path, data: previous?.path === path ? previous.data : undefined, error: errorMessage(failure) }));
      });
    return () => { current = false; };
  }, [path, nonce, refresh]);
  const reload = useCallback(() => setNonce((value) => value + 1), []);
  const shown = result?.path === path ? result : null;
  return { data: shown?.data, error: shown?.error, loading: path !== null && shown === null, reload };
}

const numbers = new Intl.NumberFormat("en-US");

export function count(value: number | null | undefined): string {
  return value === null || value === undefined ? "—" : numbers.format(value);
}

export function percent(value: number | null | undefined, digits = 1): string {
  return value === null || value === undefined ? "—" : `${value.toFixed(digits)}%`;
}

export function duration(ms: number | null | undefined): string {
  if (ms === null || ms === undefined) return "—";
  return ms < 1000 ? `${Math.round(ms)} ms` : `${(ms / 1000).toFixed(1)} s`;
}

export function dateLabel(value?: string | null): string {
  if (!value) return "—";
  const date = new Date(value);
  return Number.isNaN(date.valueOf()) ? "—" : new Intl.DateTimeFormat(undefined, { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }).format(date);
}

/** sla_placeholder -> "SLA placeholder" */
export function words(value: string): string {
  const text = value.replaceAll("_", " ").replace(/\b(sla|id)\b/g, (word) => word.toUpperCase());
  return text.charAt(0).toUpperCase() + text.slice(1);
}

/** unmapped_rows_pct -> ["Unmapped rows", "%"] */
export function signalName(signal: string): [name: string, unit: string] {
  return [words(signal.replace(/_pct$/, "")), signal.endsWith("_pct") ? "%" : ""];
}

/** The outcome a run logged, in words. */
export function runResult(run: Run): string {
  const checks = run.message?.match(/^checks=(ok|warn|fail)$/)?.[1];
  return checks === "ok" ? "Checks within limits" : checks === "warn" ? "A check raised a warning" : checks === "fail" ? "A check is over its limit" : run.message || "—";
}
