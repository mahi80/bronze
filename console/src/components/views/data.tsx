"use client";

// What the data looks like before any rule touches it: the fixed-size profile the Data Quality Agent reads,
// and the first rows of bronze exactly as they were delivered.

import type { ColumnProfile, DataProfile, DataRows } from "../api";
import { count, percent, useApi, words } from "../api";
import type { Column } from "../ui";
import { DataTable, Loading, Meter, Notice, Panel, Raw } from "../ui";

const ROW_LIMIT = 100;

export function DataView({ refresh }: { refresh: number }) {
  const profile = useApi<DataProfile>("data/profile", refresh);
  const table = useApi<DataRows>(`data/rows?limit=${ROW_LIMIT}`, refresh);
  const heading = (
    <div className="page-heading">
      <div>
        <h1>Data</h1>
        <p>Bronze exactly as delivered: the first {ROW_LIMIT} rows, and the profile that describes every row — what is missing, what repeats, and what sits in the wrong column. The same profile, never the rows, is what the Data Quality Agent reads.</p>
      </div>
    </div>
  );
  if (profile.error || table.error) return <>{heading}<Notice tone="danger">{profile.error || table.error}</Notice></>;
  if (!profile.data || !table.data) return <>{heading}<Loading /></>;
  const data = profile.data;
  const columns = data.columns;
  if (!data.initialised || !columns) {
    return <>{heading}<Notice>No file has been loaded yet. Run the pipeline once and this screen fills from bronze.</Notice></>;
  }

  const rows = data.rows ?? 0;
  const blankCells = columns.reduce((total, column) => total + column.blank, 0);
  const withBlanks = columns.filter((column) => column.blank > 0).sort((a, b) => b.blank - a.blank);
  const masked = columns.filter((column) => column.tags.sensitivity !== "none");
  const cross = data.cross_column;
  const nearDuplicateRows = cross?.near_duplicates.reduce((total, finding) => total + finding.rows, 0) ?? 0;
  const swappedRows = cross?.vocabulary_overlap.reduce((total, finding) => total + finding.rows, 0) ?? 0;
  const reversedBy = cross?.timestamp_order.filter((finding) => finding.rows_where_later_precedes_earlier > 0) ?? [];

  const columnTable: Column<ColumnProfile>[] = [
    { key: "column", label: "Column", render: (column) => <code className="nowrap">{column.column}</code> },
    { key: "type", label: "Type", render: (column) => <span className="muted">{words(column.tags.semantic_type)}</span> },
    { key: "sensitivity", label: "Sensitivity", render: (column) => column.tags.sensitivity === "none" ? <span className="muted">None</span> : <span className="tag warning">{words(column.tags.sensitivity)}</span> },
    { key: "blank", label: "Blank rows", align: "right", sortBy: (column) => column.blank, render: (column) => (
      <span className="meter-cell">{count(column.blank)}<Meter share={rows ? column.blank / rows : 0} label={`${percent(column.blank_pct, 2)} of rows blank`} />
        <span className="muted">{percent(column.blank_pct, 2)}</span></span>
    ) },
    { key: "distinct", label: "Distinct", align: "right", sortBy: (column) => column.distinct, render: (column) => count(column.distinct) },
    { key: "length", label: "Length", align: "right", sortBy: (column) => column.max_length, render: (column) => column.min_length === null ? <span className="muted">—</span> : `${column.min_length}–${column.max_length}` },
    { key: "padded", label: "Padded", align: "right", sortBy: (column) => column.padded_whitespace, render: (column) => column.padded_whitespace ? <span className="tag warning">{count(column.padded_whitespace)}</span> : <span className="muted">—</span> },
    { key: "values", label: "Most common values", render: (column) => column.top_values.length
      ? column.top_values.slice(0, 4).map((value) => <span className="tag" key={value.value}>{value.value} <span className="muted">×{value.rows}</span></span>)
      : <span className="muted">{column.note ? "withheld" : "—"}</span> },
  ];

  const rowTable: Column<DataRows["rows"][number]>[] = [
    { key: "file_row", label: "Row", align: "right", render: (row) => <span className="muted">{String(row.file_row)}</span> },
    ...table.data.columns.map((name): Column<DataRows["rows"][number]> => ({
      key: name, label: <code>{name}</code>, render: (row) => <Raw value={row[name]} />,
    })),
  ];
  const rowKey = (row: DataRows["rows"][number]) => `${String(row._source_file)}#${String(row.file_row)}`;

  return (
    <>
      {heading}

      <div className="stat-trio">
        <Panel title="Missing values" count={blankCells} description={`Blank cells among ${count(rows * columns.length)}. A blank is not the same as a bad value: silver decides which blanks break a rule.`}>
          <BarList items={withBlanks.map((column) => ({ name: column.column, rows: column.blank, of: rows }))} empty="No column has a blank cell." />
        </Panel>
        <Panel title="Across columns" description="Problems column statistics cannot show, from three scans over bronze.">
          <div className="bar-list">
            <div><span>Near-duplicate rows <span className="muted">identical except the key and up to two columns</span></span><strong>{count(nearDuplicateRows)}</strong></div>
            <div><span>Values in the wrong column <span className="muted">a column holding another column&apos;s vocabulary</span></span><strong>{count(swappedRows)}</strong></div>
            <div><span>Timestamps out of order <span className="muted">later event before the earlier one</span></span><strong>{count(reversedBy.reduce((total, finding) => total + finding.rows_where_later_precedes_earlier, 0))}</strong></div>
            <div><span>Candidate key <span className="muted">checked for uniqueness and shapes</span></span><strong><code>{cross?.candidate_key ?? "—"}</code></strong></div>
          </div>
        </Panel>
        <Panel title="Sensitive columns" count={masked.length} description="Tagged at landing from value shapes. A personal-data column is profiled by shape and count only, so its values never reach a prompt; the other tags mark what a reader should treat with care.">
          {masked.length ? <div className="bar-list">{masked.map((column) => (
            <div key={column.column}><span><code>{column.column}</code> <span className="muted">{column.tags.sensitivity === "personal_data" ? "values withheld" : "values shown"}</span></span><strong><span className="tag warning">{words(column.tags.sensitivity)}</span></strong></div>
          ))}</div> : <div className="panel-body muted">No column is tagged sensitive.</div>}
        </Panel>
      </div>

      <Panel title="Column profile" description={data.purpose ? `${words(data.table ?? "")}: ${data.purpose}` : undefined}>
        <DataTable stack columns={columnTable} rows={columns} rowKey={(column) => column.column} minWidth={1100} />
        {(cross?.near_duplicates.length || cross?.vocabulary_overlap.length || reversedBy.length) ? (
          <div className="table-footer">
            {cross?.near_duplicates.map((finding) => `${finding.groups} groups identical except ${finding.identical_except.join(", ")}; `)}
            {cross?.vocabulary_overlap.map((finding) => `${finding.column} holds ${finding.holds_values_of} values in ${finding.rows} rows (e.g. ${finding.example}); `)}
            {reversedBy.map((finding) => `${finding.later_column} precedes ${finding.earlier_column} in ${finding.rows_where_later_precedes_earlier} rows; `)}
          </div>
        ) : null}
      </Panel>

      <Panel title={`First ${ROW_LIMIT} rows`} count={table.data.total} description="bronze.tickets_raw, in the order the file delivered them. Nothing is cleaned here: this is the row silver starts from, and the row Ticket trace shows on its way back.">
        <DataTable columns={rowTable} rows={table.data.rows} rowKey={rowKey} minWidth={2000} />
        <div className="table-footer">Showing {count(table.data.rows.length)} of {count(table.data.total)} rows in bronze.</div>
      </Panel>
    </>
  );
}

function BarList({ items, empty }: { items: { name: string; rows: number; of: number }[]; empty: string }) {
  if (!items.length) return <div className="panel-body muted">{empty}</div>;
  return (
    <div className="bar-list">
      {items.map((item) => (
        <div key={item.name}>
          <span><code>{item.name}</code></span>
          <strong>{count(item.rows)}</strong>
          <Meter share={item.of ? item.rows / item.of : 0} label={`${item.rows} of ${item.of} rows blank`} />
        </div>
      ))}
    </div>
  );
}
