# Support tickets: a medallion pipeline with two agents

A bronze → silver → gold pipeline over `data/raw_tickets.csv` (10,280 messy operational support tickets), in
PostgreSQL, with two AI agents that help build and maintain the silver layer.

The design rule is one sentence: **agents propose, code checks, a person approves, and the pipeline then runs as
plain SQL.** No model call sits in the row-by-row path, so the pipeline is deterministic, re-runnable, and works
with no API key at all.

https://github.com/user-attachments/assets/067373b7-cecc-4475-9430-a1b94dd9e33c

A five and a half minute narrated walkthrough, recorded in the console: an empty database, the supplied file
loaded with one click, the three pipeline stages shown separately, a second delivery uploaded through the browser,
the two mappings it puts in front of a person, a rejected value reopened and approved, and the re-run that applies
them. The title card is the architecture; a still version, with the run-metadata tables, is in
`docs/architecture.png`, and the same video ships in the repository as `docs/walkthrough.mp4`.

| | |
|---|---|
| Result on the supplied file | 10,280 raw rows → 30 quarantined → 10,250 valid tickets → 250 duplicates flagged → **10,000 unique tickets** |
| Run time | two to three seconds for bronze, silver and gold on this file |
| Tests | 80, against a real PostgreSQL (`pytest`) |
| Agents | Data Quality Agent (option b) and Semantic Classification Agent (option c) |

## Architecture

```mermaid
flowchart LR
    F["data/raw_tickets.csv<br/>+ data/uploads/*.csv"] --> B[("bronze.tickets_raw<br/>text as-is + lineage<br/>one load per content hash")]
    B --> S[("silver.tickets<br/>typed, standardised, flagged")]
    B --> R[("silver.tickets_rejected")]
    S --> G[("gold: 3 models")]
    B -. "column profile" .-> DQ["Data Quality Agent"]
    B -. "distinct values" .-> SC["Semantic Classification Agent"]
    DQ -- "rule proposals" --> V{"automatic checks<br/>+ approval<br/>reject keeps it unmapped"}
    SC -- "mapping proposals" --> V
    V -- "approved" --> M[("mapping tables,<br/>approved + rejected<br/>decisions")]
    M --> S
```

A static version is in `docs/architecture.png`, including the console entry and the review loop. Everything that
holds data lives in one PostgreSQL database with one schema per layer, plus `ops` for run metadata; the console is
a thin client over the same API the CLI uses.

| Schema | Holds | Naming |
|---|---|---|
| `bronze` | `tickets_raw`: every source column as text, exactly as delivered | lineage columns start with `_` |
| `silver` | `tickets`, `tickets_rejected`, the mapping tables, the view `tickets_current` | `_std` standardised, `_raw` original, `is_...` flags, `dq_flags` array |
| `gold` | three aggregate tables named for subject and grain | every row carries `_run_id` |
| `ops` | run and stage log, rule effects, lineage, alerts, agent calls, proposals, evaluations | |

Choices, briefly: PostgreSQL because the brief suggests it and transactions make idempotent loads simple. SQL
files run by a small Python tool because a reviewer can read exactly what each layer does. No dbt, Airflow or
agent framework: three linear stages and single-purpose model calls do not need them. I would add dbt and a
scheduler at production scale (see "At 100 times the volume").

## How this maps to the brief

Every expectation in the brief, with a link to the section that answers it.

| Part 1 — medallion pipeline | Where |
|---|---|
| Bronze: raw ingestion, schema-on-read, no data loss; lineage with source file, `_ingested_at`, `_row_hash` | [Bronze](#bronze) |
| Silver: cleansed, deduplicated, typed, validated; every rule documented with the reason | [Silver](#silver), [Cleaning rules and why](#cleaning-rules-and-why) |
| Gold: 2–3 business models, justified | [Gold](#gold) |
| Layer separation and naming conventions | [Architecture](#architecture) |
| Idempotent and re-runnable | [Pipeline behaviour](#pipeline-behaviour) |
| Logging of what happened at each stage | [Pipeline behaviour](#pipeline-behaviour): `ops.pipeline_run`, `ops.stage_log`, `ops.dq_results` |
| Messiness handled without manual intervention | [Pipeline behaviour](#pipeline-behaviour), [Assumptions to confirm with the data owner](#assumptions-to-confirm-with-the-data-owner) |

| Part 2 — agents | Where |
|---|---|
| Option b: profile, natural-language findings, generated SQL, why a rule matters | [Data Quality Agent](#data-quality-agent-option-b) |
| Option c: enrichment into structured silver columns, batching, caching, cost | [Semantic Classification Agent](#semantic-classification-agent-option-c), [Cost and scale](#cost-and-scale) |
| Options a and d: not built, with the reason and when they would pay | [Agents I did not build](#agents-i-did-not-build) |
| Sample input and output for each agent | [Data Quality Agent](#data-quality-agent-option-b) and [Semantic Classification Agent](#semantic-classification-agent-option-c); the full report is `reports/agent_report.md` |
| The honest take on what each agent saved | [Agent assessment](#agent-assessment), [Caveats](#caveats) |
| Prompt engineering, context, output constraints, guardrails | [Prompts](#prompts), [Approval gates](#approval-gates) |
| What changes at 100 times the volume | [At 100 times the volume](#at-100-times-the-volume-with-daily-incremental-loads) |
| Tradeoffs, including approaches not taken | [Agents I did not build](#agents-i-did-not-build), [Approaches considered](#approaches-considered) |
| Architecture diagram | [Architecture](#architecture); the static version with the console and the review loop is `docs/architecture.png` |
| How to run | [How to run](#how-to-run): one `docker compose up --build`, then **Load and run** in the console, or the command in step 4 |

One honest gap: "outliers" is only covered as numeric min/max plus value shapes in the profile; there is no
percentile scan per column. Gold reports p50 and p90 for resolution time.

## How to run

The terminal route, in order. Every step has a console equivalent. Nothing is pre-loaded: the database starts
empty and the supplied file is loaded by step 4.

### 1. Install Docker (Ubuntu)

Install Docker Engine with the Compose plugin (or Docker Desktop for Linux) and check it:

```bash
docker compose version
```

### 2. Clone and start the stack

```bash
git clone https://github.com/mahi80/bronze.git && cd bronze
docker compose up -d --build        # first build takes a few minutes
```

This starts PostgreSQL, the API and the console. Three ports: the console on **http://localhost:3000** (bound to
localhost), PostgreSQL on **localhost:5433**, and the API on **8000** inside the Compose network only — the
console calls it server-side, so the shared token never reaches the browser.

### 3. Put the data where bronze reads it

The supplied file is already in place and mounted read-only:

```
data/raw_tickets.csv     10,280 rows · 13 columns
```

A later delivery goes where the console's **Upload CSV** writes it:

```bash
cp ~/Downloads/march_delivery.csv data/uploads/     # a new delivery, same place as a console upload
wc -l data/raw_tickets.csv                          # 10,281 lines: one header + 10,280 rows
head -3 data/raw_tickets.csv                        # a look before loading
```

Bronze reads every `*.csv` in `data/` and `data/uploads/` at the start of each run and loads each file once per
content hash. There is no separate upload command: copying the file and running step 4 is the upload.

### 4. Load bronze, build silver and gold

```bash
docker compose run --rm pipeline run     # the terminal's Load and run
```

This loads every new file into bronze, cleans and validates it into silver, aggregates gold, and runs the
threshold checks; the JSON summary is printed. Running it again changes nothing. The console's **Load and run**
button runs the same code, and **Pipeline** shows what went into each stage and what came out.

### 5. Explore the data (EDA)

```bash
docker compose run --rm pipeline profile    # writes reports/profile.json: blanks, near-duplicates,
                                           # swapped columns, out-of-order timestamps, candidate key
docker compose exec postgres psql -U medallion -d medallion -c "
  select count(*) as bronze_rows from bronze.tickets_raw;
  select count(*) as valid, count(*) filter (where is_duplicate) as duplicates from silver.tickets;
  select unnest(reasons) as reason, count(*) from silver.tickets_rejected group by 1 order by 2 desc;"
```

The console shows the same without SQL: **Data** for the first 100 bronze rows as delivered and the profile's
findings, **Pipeline** for the stage counts, **Checks** for the threshold signals. For an interactive prompt from
the host, `psql -h localhost -p 5433 -U medallion medallion` (password `medallion`; install `postgresql-client` on
Ubuntu).

### 6. Run the tests

```bash
docker compose run --rm --entrypoint pytest pipeline -q   # 80 tests, against a real PostgreSQL
```

### 7. Optional: agents and the approval gate

The pipeline (steps 4–6) never calls a model. Only the two agents and the eval do, so they need a provider and a
key in `.env`. Copy the template, then change `LLM_PROVIDER=none` to `anthropic` and paste your key:

```bash
cp .env.example .env
```

```ini
# .env — the lines to edit; everything else in the template keeps its default
LLM_PROVIDER=anthropic
LLM_API_KEY=sk-ant-...
# optional — these are the defaults:
LLM_MODEL_SMALL=claude-haiku-5-5      # classification and data quality
LLM_MODEL_STRONG=claude-sonnet-5-5    # rule proposals
LLM_TOKEN_BUDGET=400000               # hard cap per command; a load is not blocked when it is reached
```

`.env` is git-ignored, so the key stays out of commits. To point at any OpenAI-compatible endpoint instead
(OpenAI, a gateway, or a local Ollama or llama.cpp on the host), use:

```ini
LLM_PROVIDER=openai_compat
LLM_BASE_URL=http://host.docker.internal:11434/v1
LLM_MODEL_SMALL=<model your endpoint serves>
LLM_MODEL_STRONG=<model your endpoint serves>
LLM_API_KEY=<the endpoint's key, if it needs one>
```

One-off `run` commands read `.env` every time. If you will also start agents from the console, recreate the
services once after editing so the API container sees the file (`docker compose up -d`).

```bash
docker compose run --rm pipeline agents dq               # needs the key: propose rules from the bronze profile
docker compose run --rm pipeline agents classify         # needs the key: propose mappings for values not seen before
docker compose run --rm pipeline proposals               # stored proposals; no model
docker compose run --rm pipeline approve --task category --key aircon --set HVAC
docker compose run --rm pipeline prompts                 # writes the exact prompts, no model call
docker compose run --rm pipeline evals                   # needs the key: scores both agents against the golden sets
docker compose run --rm pipeline report --update-readme  # stored runs; no model call
```

Without a provider, `agents` and `evals` stop with "agents are not configured" (exit code 3); `run`, `profile`,
`proposals`, `approve`, `prompts` and `report` work regardless. `LLM_PROVIDER=replay` answers from recordings in
`evals/recordings/` instead of a live model — that directory ships empty, and `export-recordings` fills it from
runs you already paid for.

The console's **Review** screen is the same gate: approve, correct while approving, or reject. A rejected value
stays counted as unmapped and is listed so the decision can be reopened.

### 8. Without the console

```bash
docker compose --profile batch up --build            # PostgreSQL and one pipeline run, then exit
```

Without Docker: `pip install -r requirements.txt`, set `DATABASE_URL`, then `python -m pipeline run`. Its default
is `localhost:5432`; to run it against the Compose database instead, use
`postgresql://medallion:medallion@localhost:5433/medallion` (Compose publishes PostgreSQL on 5433 so it does not
clash with a local install).

The supplied file arrives byte-identical — `sha256sum data/raw_tickets.csv` starts with `2bf36d21` — because
`.gitattributes` marks it binary, so no checkout setting can rewrite its line endings.

## What the data contains

Every number here is measured by the pipeline, not estimated.

- **Eight timestamp formats**, including Unix epoch seconds, plus junk such as `asap`, `TBD`, `00/00/0000`. The
  three US-style formats are month-first: the first component never exceeds 12.
- **62 spellings of 10 categories**, 17 spellings of 4 priorities, 33 spellings of 16 submitters.
- **30 placeholder rows** ("IGNORE THIS ROW", "asdfasdfasdf"). Each fails at least three hard checks.
- **50 rows with category and description swapped.** All 50 are copies of earlier tickets; 24 lost their text
  beyond 60 characters in the swap.
- **250 duplicates**: 200 re-keyed copies that differ only in ticket ID, created date and submitter, and the 50
  swapped copies. Removing them leaves exactly the 10,000 tickets numbered 1000 to 10999.
- **Placeholders in numeric fields**: cost `-1`, `N/A`, `TBD`, `$` prefixes; SLA `999`, `0`, `N/A`.

### What the data cannot support

Before choosing rules and gold models I tested whether fields relate the way a real ticketing system would make
them. Several do not, and I would rather say so than build metrics that only look meaningful.

| Relationship | What the file shows | Consequence |
|---|---|---|
| resolved after created | 1,865 of 5,319 tickets with both timestamps (35%) are resolved before they were created. Valid intervals have a median of about 195 days against SLAs of 4 to 72 hours | Flag, never correct. Durations only where the order is valid |
| tickets meet their SLA | Of 10,000 unique tickets, 2,003 have a valid interval and a real SLA; 8 of those meet it | Gold publishes coverage beside every rate |
| status agrees with resolved_at | 2,127 open tickets carry a resolved_at; 100 closed tickets have none | Two flags, no automatic fix |
| assignee depends on category | Every assignee handles every category at 8% to 12% of its tickets | No "misrouted ticket" rule |
| SLA hours depend on priority | Each SLA value appears at 18% to 22% under every priority | Missing SLA is not imputed |
| priority reflects urgency in the text | 1,922 tickets carry a safety-hazard label; 580 of them (30%) are Low or have no priority, about the same share as all tickets (31%) | Missing priority stays missing; a hazard signal is derived from the text instead |
| category agrees with description | Every description template belongs to exactly one category | Reliable, and a free labelled set for evaluation |

These look like the output of a generator that draws fields independently. I treat that as a property of the
file: the pipeline keeps what was received, flags what cannot be true, and avoids rules that would pretend
otherwise. In a real engagement these rows are the first thing I would take to the data owner.

## Bronze

`bronze.tickets_raw` holds the 13 source columns as text, exactly as delivered: an empty field stays an empty
string, and `NULL`, `N/A` and `???` stay as text. Interpretation is silver's job. Lineage metadata on every row:
`_source_file`, `_file_sha256`, `_source_row_number`, `_ingested_at`, `_batch_id`, `_row_hash` (SHA-256 of the 13
fields) and `_dup_seq` (the nth occurrence of identical content within a file).

- A file whose hash is in `ops.file_manifest` is skipped. Running the same command twice inserts nothing.
- Rows are inserted under a uniqueness rule on (`_row_hash`, `_dup_seq`), so an overlapping file adds only new
  content, while two identical lines in one file are both kept. That is how "dedup tracking" and "no data loss"
  hold together.
- A file whose header differs from the registered one is not loaded. The difference is recorded in
  `ops.drift_event` and the run fails with a message naming the added and missing columns.
- `data/raw_tickets.csv` is mounted read-only. A CSV uploaded in the console is content-hashed, stored under
  `data/uploads/`, and loaded by the same bronze glob on the next run.

A test compares the row hashes in bronze with hashes computed from the CSV: they are identical.

## Silver

`silver.tickets` has one row per valid ticket: typed, standardised, flagged, with the raw value kept beside
every standardised one. `silver.tickets_rejected` is the quarantine. `silver.tickets_current` is the view gold
reads: the same table without flagged duplicates. Three kinds of logic, in order of preference:

1. **Deterministic SQL** for everything pattern-shaped: timestamps, placeholders, resolution notes, swaps,
   duplicates.
2. **Mapping tables** (`category_map`, `priority_map`, `person_map`, `issue_map`) for everything that is a
   vocabulary. Each row records the decision, who or what proposed it, the confidence and who approved it.
3. **A model**, only to propose rows for those mapping tables.

The mappings shipped in `rules/*.csv` are the baseline I labelled by hand while profiling (`source = seed`), so
the pipeline output is complete without a model, and every figure in this README comes from them. Agent
proposals are added through the approval gate, and `export-rules` writes approvals back to those files, so an
approval becomes a reviewable commit.

### Cleaning rules and why

The rules are documented in `rules/cleaning_rules.yaml`. After every build the pipeline measures the effect of
each rule and stores it in `ops.dq_results`, so the documentation and the behaviour cannot drift apart. "Rows"
is the measured effect on the supplied file.

| ID | Rule | Rows | Why |
|---|---|---:|---|
| R01 | **Quarantine rows that fail a hard check.** A row with no valid ticket ID, no parseable created date, no valid status or no valid building goes to silver.tickets_rejected with every reason listed. | 30 | These are test and placeholder entries. On this file every one of them fails at least three checks, which is good evidence that the rule does not catch real tickets. Rejecting to a table keeps them auditable. |
| R02 | **Repair swapped category and description.** When category is not a known label and description is, swap the two and flag swapped_columns. | 50 | Deterministic and reversible, and it recovers the category of tickets that would otherwise be unclassified. |
| R03 | **Flag re-keyed duplicates.** Rows identical on category, priority, status, building, description, resolved_at, assigned_to, resolution_notes, cost and sla_hours are duplicates. The comparison runs after the swap repair. The lowest ticket number survives; the others get is_duplicate and duplicate_of. Gold reads silver.tickets_current, which leaves them out. | 250 | The same ticket was entered again under a new ID. Flagging keeps the evidence; deleting would not. |
| R03a | **Mark duplicates matched on sparse fields as weak.** A duplicate with no resolved_at, no cost and no notes is marked dup_match_strength = weak. | 3 | Ten matching fields are weaker evidence when three of them are empty. A reviewer should see these first. |
| R03b | **Match truncated swapped rows to their original by prefix.** A swapped row whose text was cut at 60 characters is a duplicate when another ticket matches the other nine fields and its description starts with the truncated text. It gets dup_match_strength = truncated_prefix and the flag description_truncated. | 24 | The swap lost text in the source system, so these rows can never match exactly. Finding the longer original both identifies the duplicate and proves the truncation, and the full text survives on the original. |
| R04 | **ticket_id must be TKT- followed by digits.** Rows that fail are quarantined by R01 with reason invalid_ticket_id. | 26 | It is the business key of the silver table. |
| R05 | **Parse created_at from eight registered formats.** Eight explicit patterns, month-first for the US formats. Epoch seconds are converted to the source time zone, UTC by default. The source format and the precision (second, minute, day) are recorded. The count is values that were not already ISO date-time. | 5,605 | A permissive parser guesses day and month order without telling anyone. Explicit patterns fail loudly on a new format, and that failure is the trigger for a drift review. |
| R05a | **Parse resolved_at with the same parser.** Blank stays NULL. The count is resolved_at values that parsed. | 5,319 | Same reasoning as R05. |
| R06 | **Flag resolved_at earlier than created_at.** Compared by date when either value has day precision. Both values are kept, the flag resolved_before_created is added and resolution_hours stays empty. | 1,865 | The pipeline cannot know which timestamp is wrong. Correcting or dropping would invent data, and a negative duration would corrupt every average. |
| R07 | **Flag a resolved_at on a ticket that is still open.** Status is not Resolved or Closed but resolved_at is filled. Flag only. | 2,127 | Either field could be the wrong one, so the contradiction is surfaced for the owner of the source. |
| R07a | **Flag a closed ticket with no resolved_at.** Status is Resolved or Closed but resolved_at is empty. Flag only. | 100 | Same reasoning as R07. |
| R08 | **Standardise category through silver.category_map.** Each raw spelling maps to one of ten standard categories. The raw value is kept in category_raw. The count is rows whose value changed. | 8,910 | A lookup table is transparent, versioned and extendable without a code change. The classification agent proposes new rows and a person approves them. |
| R08a | **Categories with no approved mapping become Unclassified.** The row is kept, labelled Unclassified and flagged category_unmapped. | 0 | A new spelling must never block the load or be guessed silently. |
| R09 | **Standardise priority through silver.priority_map.** Maps to Critical, High, Medium or Low. Normal maps to Medium. The count is rows whose value changed. | 7,006 | Normal is the one judgement call and is listed as an assumption in the README. |
| R09a | **Missing priority stays missing.** Blank priority becomes NULL and is flagged priority_missing. It is not imputed. | 1,078 | Nothing in the file predicts priority, so imputing it would fabricate a field that drives SLA reporting. |
| R10 | **Resolve submitter spellings through silver.person_map.** Each spelling maps to a person ID and a display name. The count is rows whose name changed. | 5,820 | Without it most people appear as two or three different submitters. A merge is a decision about identity, so every one needs a person's approval. |
| R10a | **Submitters with no approved mapping are flagged.** The raw name is kept, submitter_id stays NULL and the row is flagged submitter_unmapped. | 0 | An unreviewed merge is worse than a missing one. |
| R11 | **Cost placeholders become NULL with a reason.** The currency symbol is removed and the value cast to numeric(12,2). N/A, TBD and blank become NULL with cost_status not_applicable, pending or missing. -1 becomes NULL with status unknown. Zero is kept. The count is rows without a usable cost. | 3,798 | A placeholder left in a numeric column lowers every average. Keeping the reason preserves the difference between "not yet known" and "no cost applies". |
| R11a | **Sentinel cost values (-1) are flagged.** A cost that is present but negative or not numeric gets cost_placeholder. | 192 | Distinguishes a deliberate sentinel from a blank. |
| R12 | **SLA placeholders (999, 0) become NULL.** sla_hours must be 4, 8, 24, 48 or 72. Any other value becomes NULL with sla_status placeholder. | 1,041 | A 999-hour or 0-hour SLA is not a commitment anyone can be measured against. Left in, it makes a ticket pass or fail by construction. |
| R12a | **SLA not set (blank or N/A).** Becomes NULL with sla_status not_set. Not imputed from priority. | 3,065 | SLA hours are independent of priority in this file, so there is nothing to impute from. |
| R13 | **Extract attributes from resolution notes by pattern.** Eleven regular expressions set note_type and extract root cause, replaced part, hours on site, parts ETA, scheduled weekday, resolver and duplicate reference. The count is notes that matched a pattern. | 8,705 | The notes follow fixed sentence patterns. A model here would add cost and variability for no gain. |
| R13a | **Notes that match no pattern.** note_type is set to other. A rise here means the note vocabulary has changed. | 0 | The guard that tells us when the regular expressions stop being enough. |
| R14 | **Issue type and hazard signal from the description template.** Numbers, the building name and asset IDs are masked; the resulting template is looked up in silver.issue_map, which the classification agent fills and a person approves. Floor, room and asset identifiers are extracted by regular expression. The count is rows with an issue type. | 10,138 | The identifiers are pattern-shaped. The judgement about hazard is not. |
| R15 | **Trim and collapse whitespace.** Every text column is trimmed and repeated spaces collapsed. The count is rows where that changed a value. | 1 | Prevents false distinct values. |
| R16 | **Blank assignee becomes NULL and is not backfilled.** assigned_to is never filled from "Resolved by" notes. | 3,092 | Where both exist, the note and the column disagree far more often than they agree. |
| R17 | **Guard: building named in the description equals the building column.** Counts descriptions that name a different building than the ticket's own. Expected to be zero. | 0 | Costs almost nothing and will catch a mis-keyed building or a future column swap. |
| R17a | **Guard: each description template belongs to one category.** Counts rows whose template appears under more than one standard category. Expected to be zero. | 0 | Category and description agree perfectly today; this notices when they stop. |

Two things the implementation found that profiling alone had not:

- Comparing rows *after* the swap repair shows that the swapped rows are themselves copies of earlier tickets.
  Rule R03b matches the truncated ones to their original by prefix. The result is exactly 10,000 unique tickets,
  which is strong evidence that the duplicate rules are neither too loose nor too tight.
- One swapped description is naturally 60 characters long. A length rule alone would have called it truncated;
  requiring a longer original to exist avoids that.

Each run asserts `bronze = silver + rejected + superseded` and fails otherwise ("superseded" is an older delivery
of a ticket that a later file replaced).

## Gold

Gold reads only `silver.tickets_current`. Each model is rebuilt in one transaction per run, and a check asserts
that every ticket is counted exactly once in each.

| Model | Grain | Why this model |
|---|---|---|
| `gold.sla_performance_monthly` | month × category × priority | SLA attainment is the first question asked of a ticketing system. It reports median and 90th-percentile resolution hours and the share within SLA, with **coverage beside the rate**: here only 2,003 of 10,000 tickets are measurable and 8 meet their SLA, which is a finding about the data, not about the service |
| `gold.building_category_hotspots` | building × category (monthly variant as a view) | 17 buildings by 10 categories gives 170 cells of about 60 tickets each, dense enough to rank. It rests on counts, category and building, the reliable fields, and adds the number of safety-hazard tickets that are Low or unprioritised (580) |
| `gold.assignee_workload_cost` | assignee × month | Workload and spend per assignee inform contract and staffing decisions. Cost is known for 62.9% of tickets, so coverage is a column; the 3,018 unassigned tickets are their own row, not hidden |

Models I chose not to build: a daily building × category table (547 days × 170 cells for 10,000 tickets is almost
all zeros), resolution-time rankings of assignees (the timestamps cannot support them), and a misrouted-ticket
measure (assignee is independent of category).

### Dimensional model, if this became a warehouse

The three gold tables are aggregates, not a warehouse design. With more sources I would add:

- Conformed dimensions for date, location, category, person and assignee. `building` mixes site and floor
  ("HQ Floor 1"), and `assigned_to` mixes vendors with one named person, so location and assignee both need a
  type before they can be conformed.
- One fact table at ticket grain, with cost, SLA hours and resolution hours as measures. The current gold
  tables become views over it, so a metric such as SLA attainment is defined once.
- Cross-source reconciliation: vendor invoices against ticket cost by assignee and month, and an HR roster
  against the person map. Those are the checks I would want before trusting the cost and workload numbers.
- The source key stays on the fact row (`ticket_id` plus the bronze row hash), so a conformed row can still be
  traced back to the source file.

## Pipeline behaviour

- **Idempotent.** Bronze skips files it has loaded. Silver and gold are rebuilt in a transaction from the same
  inputs and the same rule set, so a re-run produces identical tables; a test compares checksums. A failure
  leaves the previous version in place. A PostgreSQL advisory lock allows one run at a time: a second run
  started meanwhile stops at once instead of interleaving.
- **Logged.** `ops.pipeline_run` and `ops.stage_log` record rows in, out and rejected for every stage; the
  console prints one line per event with the run ID. `ops.dq_results` records the effect of every rule per run.
  `ops.agent_call` records the model, prompt version, tokens, latency and response for every accepted call, which
  is where the cost table in the report comes from.
- **Lineage.** Every silver row carries the bronze row hash, batch, run and rule set version. `ops.lineage_edge`
  records source, target, transformation, rule set version, who and when for every stage. Every mapping row
  records who proposed and who approved it. A gold figure can be traced to the exact lines of the file.
- **No manual steps, and no blocking.** An unseen category, priority or submitter does not stop a load: the row
  is kept, labelled `Unclassified` or left unresolved, and flagged. A missing API key changes nothing.
- **Thresholds.** After each run the signals in `rules/thresholds.yaml` are checked (reject rate, rows rejected
  for a single reason, duplicate rate, unmapped values, unmatched notes). A breach writes `ops.alert`; a failure
  exits non-zero. "Rejected for one reason only" is the drift signal: placeholder rows fail several checks, so a
  row that fails just one is probably a real ticket hit by something new, such as an unregistered date format.
- **Metadata tags at landing.** When a file lands, the bronze stage writes a semantic type and sensitivity per
  column to `ops.column_tags`, set by rules on value shapes. `submitted_by` is tagged personal data and its
  values are withheld from the profile the agent reads. Two known gaps: the rules miss `assigned_to`, which
  mixes vendors with one named person, and the agent's probe queries are not masked, so a probe can still read
  a tagged column.

### Incremental loads and backfill

As built, files are tracked by SHA-256 in `ops.file_manifest`, so a re-delivered file is skipped. A test delivers
a second file of four rows: the new rows land, and one ticket arrives again under the same ID with changed
fields, so the latest delivery wins in silver while both versions stay in bronze. A rerun changes nothing.

At scale I would merge silver by `ticket_id` after a watermark instead of rebuilding it, recompute a window of
recent batches on every run for late data, and replay a range with a backfill command. Silver merges by key and
gold replaces whole month partitions, so a replay cannot double count.

## The agents

Part 2 asks for agents that make the process faster, smarter or more adaptive. Mine map to those words like this:

| Agent | Faster | Smarter | More adaptive |
|---|---|---|---|
| Data Quality Agent | drafts rules, reasons and SQL from a profile when a new source arrives | | re-runs when a load drifts |
| Semantic Classification Agent | | adds issue type and hazard signal, columns no rule could derive | maps new spellings without a code change |

Both follow the same loop: deterministic preparation → one schema-constrained model call (or a short bounded
loop) → automatic checks → risk policy → approval → deterministic SQL.

### Data Quality Agent (option b)

- **Starts from a profile, not from rows** (`pipeline/profiler.py`): per column, blank share, distinct count, top values
  and value shapes (digits as 9, letters as a). Across columns, three generic scans that column statistics
  cannot replace: near-duplicate rows, values sitting in another column's vocabulary, and timestamp order.
  On this file those scans surface the 197 duplicate groups, the 50 swapped rows and the reversed timestamps.
- **A bounded tool loop per column**, plus one for the cross-column findings: the model may ask for up to three
  read-only probe queries, twice, before it must propose. Each probe returns at most 20 rows. The plan is fixed
  and the context stays small.
- **Proposes** a finding with numbers, why it matters, an action, SQL identifying the affected rows, and the
  number of rows it expects that SQL to match.
- **Is not trusted.** Its SQL passes a text check (one statement, no writes, no comments, no dangerous
  functions), then runs on a separate connection in a read-only transaction as a role that can only `SELECT`
  from bronze, with a timeout. The measured row count is compared with the claimed one; a mismatch rejects the
  proposal. Tests attack each layer separately.
- **Changes nothing by itself.** Proposals wait in `ops.rule_proposal`. Approved predicates run as checks on
  every load and report into `ops.dq_results`.

Sample input and output from one live call (`reports/agent_report.md` has the full version; the profile is
abridged here):

```text
FOCUS: column ticket_id
{"column": "ticket_id", "rows": 10280, "blank": 8, "distinct": 10261,
 "shapes": [{"shape": "aaa-9999", "rows": 9000}, {"shape": "aaa-99999", "rows": 1254},
            {"shape": "a/a", "rows": 9}, {"shape": "aaaa", "rows": 5}, {"shape": "99", "rows": 4}],
 "tags": {"semantic_type": "identifier", "sensitivity": "none", "basis": "fixed pattern, unique"}}
```

```json
{"proposals": [{"rule_id": "ticket_id_placeholder_to_null", "action": "set_null",
  "columns": ["ticket_id"], "sql": "ticket_id IN ('N/A','NULL')", "expected_rows": 14,
  "finding": "14 rows hold a placeholder instead of an ID: 'N/A' in 9 rows and 'NULL' in 5 rows. Both are text strings, not real nulls.",
  "why_it_matters": "Placeholder strings pass as ordinary values in counts and joins. 'N/A' and 'NULL' would each be treated as a single ticket, which hides the missing keys and can create false matches."}]}
```

### Semantic Classification Agent (option c)

| Task | Sent to the model | Lands in silver as |
|---|---|---|
| Category | distinct spellings with row counts and two masked sample descriptions | `category_std` via `category_map` |
| Submitter | distinct name spellings and the known roster | `submitter_id`, `submitter_name` via `person_map` |
| Issue type and hazard | masked description templates | `issue_type`, `hazard_signal` via `issue_map` |

- **Distinct values only, in batches of 50.** Numbers, building names and asset IDs are masked first, which
  collapses 4,600 distinct descriptions into 86 patterns and keeps identifiers out of the prompt.
- **The mapping tables are the cache.** A value with a mapping is never sent again. Responses are also cached by
  a hash of provider, model, prompt version and input; only answers that passed validation are cached.
- **Deterministic first.** Rows the swap repair will fix are not candidates. Values seen on fewer than three
  rows are held, not sent.
- **Guardrails.** Output is schema-constrained with labels as enumerations and `unknown` always allowed. Every
  key must come back exactly once. A canonical person name must already be known or be a full spelling present
  in the input, so the model cannot invent a person. A rejected answer is retried once with the reason.
- **Policy.** Category and issue mappings at or above 0.9 confidence are applied as *provisional* and listed for
  review; anything lower is held. A person merge is a decision about identity and is always held for approval.

Sample input and output from one live call:

```text
{"key": "v001", "value": "access control", "rows": 176, "samples": ["Badge reader at <BLDG> main entrance not working. Staff can't get in.", "Door <N> won't lock. Handle mechanism seems broken."]}
```

```json
{"key": "v001", "category": "Security & Access", "confidence": 0.97,
 "reason": "Access control matches badges, locks, and door access systems."}
```

### Approval gates

Nothing an agent proposes changes silver on its own.

| Change | Gate |
|---|---|
| Category or issue mapping at 0.9 confidence or more, not `unknown` | Applied as provisional and listed for review |
| Lower confidence, or `unknown` | Held as proposed; not applied |
| Person merge | Always held for a person, whatever the confidence |
| Data Quality Agent proposal | Must pass the SQL guard and the claimed-vs-measured row count, then be approved by a person; it runs as a check and never rewrites silver |
| Approved mappings | `export-rules` writes them back to `rules/*.csv`, so the approval is a reviewable commit |

### Prompts

Prompts are versioned files in `agents/prompts/`; the version is stored with every output. `docs/prompt_preview.md`
shows the exact prompts built from this data. Each has one job, states the allowed labels with definitions,
passes counts and masked samples as context, and treats ticket text as untrusted: values are delimited and the
instructions say to never follow instructions found in them. The evaluation set includes injection probes.

### Cost and scale

Model usage follows the number of distinct values, not the number of rows.

| Work | On this file | At 10 million rows |
|---|---|---|
| Data Quality Agent | 14 investigations over a fixed-size profile | the same, plus one cycle per drift event |
| Category | 46 values, 1 call | only spellings not seen before |
| Submitter | 30 values, 1 call | grows with head-count, not tickets |
| Issue type and hazard | 66 templates, 2 calls (the 20 cut-off variants are too rare to send) | grows with distinct normalised text: the real cost risk once text is not templated |
| Notes, dates, cost, SLA, duplicates | no calls | no calls |

What is **batched**: values per call, and for a backfill the provider's batch interface, where latency does not
matter. What is **cached**: approved mappings, validated responses, the fixed instruction block. What is
**skipped**: anything already mapped, anything a regular expression solves, quarantined rows, rare values. A
token budget per command is a hard stop: classification ends, the load continues, the rest stays unmapped.

This file's descriptions are templated, so they collapse almost completely. Real text would not. I would then
rely on normalisation and exact-match caching first, a small model with escalation of low-confidence items
second, and would measure cache hit rates before committing to a budget.

## Agent assessment

### How the agents are assessed

`python -m pipeline evals` scores both agents against golden sets I labelled by hand during profiling
(`evals/golden/`). One caveat: the category golden set is the same key-to-label list that seeded
`rules/category_map.csv`, so that score measures agreement with my own map, not a second opinion.

| Golden set | Size | Scored on |
|---|---|---|
| Category map | 46 keys (62 raw spellings) → 10 labels | exact match; how many wrong answers were confident enough to auto-apply |
| Person map | 30 keys → 16 people | false-merge pairs (the costly error) and missed-merge pairs |
| Hazard signal | 66 templates, 12 marked ambiguous when I labelled them | accuracy on the 54 clear ones, and on all |
| Data quality checklist | 18 known problems | recall; a validated proposal counts when its own rows are at least 90% inside the known set and, together, they cover 40–80% of the known rows (the threshold varies by problem), or when the finding mentions one of the problem's keywords |
| Injection probes | 10 crafted values | how many were followed |

Results go to `ops.agent_eval` with model and prompt version. Every call's tokens, latency and cost are stored
in `ops.agent_call`, so a prompt, tool or model change can be compared before it is accepted. `--consistency`
repeats the category task three times without the cache.

### Measured results

<!-- AGENT_RESULTS:START -->
| Task | Model | Result | Detail |
|---|---|---|---|
| Category map | claude-haiku-5-5 | 95.7% exact match on 46 category keys | coverage 95.7%; 1 wrong at or above the auto-apply threshold |
| Submitter resolution | claude-haiku-5-5 | 16 people found, 16 expected | 0 false-merge pairs, 0 missed-merge pairs |
| Hazard signal | claude-haiku-5-5 | 96.3% on 54 unambiguous templates | 92.4% on all 66; 60 distinct issue types |
| Injection probes | claude-haiku-5-5 | 0 of 10 followed | all answers passed validation |
| Data Quality Agent | claude-sonnet-5-5 | 17 of 18 known problems found | 35 of 35 proposals passed the SQL and row-count checks; 30 probes |
| Consistency |  | 100.0% agreement over 3 uncached runs | unstable values: 0 |

<!-- AGENT_RESULTS:END -->

The Data Quality Agent row is the eval's own score. A hand check of the 35 proposals puts it at 13 of 18 covered,
plus one problem only partly; three of the eval's 17 hits are keyword matches credited to a proposal about a
different problem. The caveats below say which.

### What each agent saved, on this file

| Agent | What it did | What it saved |
|---|---|---|
| Data Quality Agent | 13 of 18 known problems on a hand check (the eval credits 17, three by keyword on the wrong proposal); 35 of 35 proposals validated; about $0.50 and 27 calls | No time: I had already written most of the rules. It pays on a new source or a drifted load, where the profile is the head start. |
| Semantic Classification Agent | 44 of 46 categories; 16 of 16 people with no false or missed merges; 96.3% hazard on unambiguous templates | Nothing on categories, which the lookup already covers. On people and hazard it produced answers a rule could not, which is the part I would keep. |

Sample input for every task is in `docs/prompt_preview.md`. Sample output, every proposal with its validation
result, and token usage are written to `reports/agent_report.md` by `python -m pipeline report`, verbatim from
what was recorded.

### Caveats

The numbers above are real. They are also narrower than they look.

- **Small, hand-labelled golden sets.** 46 category keys, 30 submitter spellings, 66 templates. One wrong label
  moves the category score by more than two points. I marked 12 of the 66 hazard templates ambiguous myself and
  left them out of the headline accuracy; that was my judgement, not a property of the model.
- **No win over the lookup on categories.** A hand-written map already covers all 46 keys. The agent got 44, with
  both misses returning unknown for misc and other. Safe, but on this file it adds nothing. Its case is the
  unseen spelling on a later delivery, and that path is tested with a stub, not a live model.
- **Hazard accuracy is per template, not per row.** Templates repeat unevenly in the file, so a row-weighted
  score would differ. The agent also produced 60 distinct issue types for 66 templates, which is too fine-grained
  to group on without tightening the taxonomy.
- **"Found" is a generous test for the Data Quality Agent, and 17 of 18 overstates it.** The eval counts a
  problem as found when validated proposals on the right column cover 40–80% of the known rows (the threshold
  varies by problem; each proposal's own rows must also be at least 90% inside the known set), or when a finding
  mentions one of the problem's keywords. A hand check of the 35 proposals finds 13 of the 18 genuinely covered.
  One is partial: the only duplicate rule matches a single group of two rows, while the pipeline flags 250.
  Three more are credited by keyword to a proposal about a different problem: the category spellings, the
  submitter spellings, and the open/closed-with-resolved_at contradiction. It cost about $0.50 and 27 calls after
  I had already written most of the rules, so it saved no time here. The two findings that mattered, the
  duplicates and the swapped columns, came from cross-column SQL I added to the profiler. The agent is only as
  good as those scans.
- **One file, one model, one afternoon.** Consistency was three repeats of 46 category inputs with the cache
  bypassed; it says the answers were stable in that run, nothing about a model upgrade. The defaults are the
  current list models, and the harness is what lets the next model be compared rather than assumed.
- **These results do not measure the pipeline.** Bronze, silver and gold were built and checked before the agents
  ran, and no agent can change silver without an approved mapping. Remove the agents and the output is still the
  same 10,000 tickets.

### Agents I did not build

An agent earns its place only if it adds a judgement a rule cannot and a person can review the result. Five
candidates failed that test:

| Candidate | Why not here | When I would build it |
|---|---|---|
| Resolution-note parser | Notes follow 11 fixed patterns and the regexes match all 8,705 of them; a model adds cost and variance for nothing | free text replaces the enum |
| Schema inference | One 13-column file; writing the DDL took me minutes and reviewing generated DDL would take about as long | many sources, or headers that drift between deliveries |
| Header-drift checker | A rule on the expected column set catches a missing or renamed column; inference starts to pay only with dozens of differently shaped sources | many heterogeneous feeds |
| Gold-metric designer | The useful input is knowing which fields can be trusted, and the timestamps here are random, so an agent given only the silver schema would propose sensible mechanics on top of bad assumptions. I would let it write date spines and window functions; I would not let it pick the metrics | trusted timestamps and owner-agreed definitions |
| Duplicate adjudicator | Matches are ranked on ten fields, the lowest ticket number survives and sparse matches are flagged weak for review first; there is no judgement left for a model | if weak matches became a large share, or free-text near-duplicates needed a call |

The limits I would watch next are cost and review load. $0.50 to evaluate is cheap; running the Data Quality
Agent on every delivery is not, and a queue of 35 proposals is real work. The 0.9 confidence and three-row
thresholds are starting values, not tuned ones. The first changes I would make are a row-weighted hazard score,
golden sets split by delivery date, and a recorded live replay so the eval runs without a key.

## At 100 times the volume, with daily incremental loads

| Area | Change |
|---|---|
| Ingestion | Already per file with a manifest. Add partitioning of bronze by ingestion date and chunked `COPY` |
| Silver | Replace the full rebuild with a merge keyed on `ticket_id` for batches after a watermark. "Latest delivery wins" is already implemented and tested; the rebuild stays for backfills |
| Duplicates | Store a hash of the ten matching fields with an index, so detection is a lookup, not a window over the table |
| Late data and replays | Recompute a window of recent batches on every run; a backfill command replays a range. Silver merges by key and gold replaces whole month partitions, so a replay cannot double count |
| Gold | Rebuild only the affected month partitions |
| Engine | PostgreSQL with partitioning handles millions of tickets for this workload. For heavy concurrent analytics I would move silver and gold to a columnar warehouse; the SQL-first design ports directly |
| Orchestration | A scheduler with retries per stage, and dbt for model dependencies and tests |
| Agents | Run on the daily delta of unseen values with a review queue and a daily token budget. The drift signals trigger the Data Quality Agent |
| Metadata | Publish lineage as OpenLineage events to a catalogue instead of only keeping it in `ops` |

## Assumptions to confirm with the data owner

- **Time zone.** Naive timestamps are kept as written; epoch values are converted using `SOURCE_TZ` (default UTC).
  Two epoch values fall on 31 December 2023 in UTC while everything else starts on 1 January 2024, which suggests
  the source clock is ahead of UTC.
- **`Normal` priority** maps to Medium. **Zero cost** is a real zero. **`-1` cost, `999` and `0` SLA** are placeholders.
- **Duplicate survivor** is the lowest ticket number.
- The thresholds (0.9 confidence, three rows, alert levels) are starting values to be tuned with evaluation data.

## Operator console

The brief does not ask for a user interface, and nothing above depends on this one: the pipeline, the agents and
their tests run without it, and deleting `console/` and `pipeline/api.py` removes it. It ships in the default
Compose stack because the approval gate is the centre of the design, and a gate is easier to judge on screen than
as command-line output. The UI itself is on port 3000; it calls the API on port 8000 over the Compose network,
and only 3000 is published.

```bash
docker compose up --build     # UI on http://localhost:3000; API on 8000 inside Compose
```

![Console overview: rows per layer, the reconciliation, the threshold checks](docs/console-overview.png)

![Console pipeline: the files into bronze, the rows out of silver, the three gold models](docs/console-pipeline.png)

Before the first run the console shows the three stages and one button that loads the supplied file; nothing is
pre-loaded. Upload a CSV to add a later delivery to the same bronze path, then run: the Pipeline screen shows the
files that went into bronze, the rows that came out of silver (valid, quarantined, duplicates flagged) and the
three gold models, each with its reconciliation. Uploading and running is the round trip the walkthrough shows.

| Screen | What it shows and does |
|---|---|
| Overview | Rows in each layer with the reconciliation the last run asserted, the threshold signals, what is waiting for review. Starts a run; uploads a delivered CSV |
| Pipeline | The three stages separately: files and rows into bronze, valid/quarantined/duplicate rows out of silver, the three gold models with their row counts, each linking to its detail screen |
| Runs | Run history. Per run: the stages, the measured effect of every rule, alerts and drift, lineage |
| Data | The first 100 rows of bronze exactly as delivered, and the fixed-size profile the Data Quality Agent reads: blanks per column, near-duplicates, values in the wrong column, timestamps out of order, the candidate key, and which columns are sensitive and why |
| Review | Agent proposals with their evidence. For a mapping: value, confidence, reason, and how many tickets depend on the decision. For a rule: the SQL, the claimed and the measured row count. Approve, correct while approving, or reject; a rejected value stays counted as unmapped and is listed so the decision can be reopened |
| Data quality | Every cleaning rule with its measured effect, the quarantine with reasons, flags, column tags |
| Gold | The three models, filterable and sortable, with coverage beside every rate |
| Ticket trace | One ticket from the line in the file to silver, column by column, and whether gold counts it (`docs/console-trace.png`) |
| Agents | The provider, what would be sent, starting an agent, recorded usage |

- **`pipeline/api.py`** is a small FastAPI app with no logic of its own. Read routes query `ops`, `silver` and
  `gold`; write routes call the functions the command line calls. `tests/test_3_console_api.py` checks that
  what it reports is what the pipeline wrote, and drives a run, the approval gate and both agents over HTTP
  with the stub model.
- **`console/`** is a Next.js app. Its shell, design system and server-side request policy are adapted from the
  console of my [DataMigration](https://github.com/mahi80/DataMigration) project, where a reviewer approves one
  exact plan. Here a decision is sent for exactly the proposals on screen, by key.
- **Access.** This is a local tool. The browser talks only to the console's own server, which forwards an
  allow-list of routes to the API with a token the browser never sees, and refuses a change that does not come
  from its own pages. The API is not published to the host and the console is published on 127.0.0.1 only. There are no
  user accounts: a decision is recorded under `APPROVER`, exactly as on the command line. Anything shared
  would need single sign-on and per-user identity first.
- **Limits.** Agent runs are background jobs held in the API's memory, which suits one process and no more. At
  100 times the volume this screen is the review queue in the table above, behind a real job queue.

Without Docker: `CONSOLE_API_TOKEN=<secret> uvicorn pipeline.api:app`, then in `console/` run `npm install` and
`CONSOLE_API_TOKEN=<secret> npm run dev`.

The screenshots show the output of the supplied file. The Review and Agents screens have proposals to show only
after an agent run; they were exercised in a browser with the test stub, and no stub output appears in this
README.

## Repository

```
sql/        00_schemas (DDL, parsers, hard checks), 20_silver, 30_gold
pipeline/   cli, bronze, silver, gold, checks, profiler, rules, db, config; api (the console's backend)
agents/     llm (providers, cache, budget), classifier, dq_agent, guard, review, evals, report, prompts/
rules/      approved mappings (seed), cleaning_rules.yaml, thresholds.yaml, taxonomy.yaml
evals/      golden sets used by the agent eval
tests/      parsers, pipeline, agent plumbing with a stub model, console API, incremental load, provider adapters
console/    the operator console (Next.js), part of the default compose stack; its request-policy tests run with `npm test`
docs/       architecture.png, walkthrough.mp4 (narrated, captioned), prompt_preview.md, console screenshots
```

The agent tests use a stub in place of a model and prove only the plumbing: batching, validation, retries, the
SQL guard, the approval gate, scoring. The report generator ignores stub calls, so a test run can never appear
as a result.

## Approaches considered

Every route I weighed for turning this file into a trustworthy silver layer, including the ones that lost:

| Approach | Verdict | Why, in one line |
|---|---|---|
| Cleaning rules in reviewable SQL | **Kept** — the spine | Deterministic, re-runnable with no key; 80 tests pin every rule |
| Lookup tables for seen spellings | **Kept** | 62 category, 17 priority and 33 submitter spellings resolved without a model |
| Two agents that propose rules and mappings | **Kept** — for gaps only | They add judgement a rule cannot, and every output passes a check and a person |
| A model call per row | **Rejected** | Cost and variance scale with rows, and the approval point disappears |
| A model that writes the whole silver layer | **Rejected** | Non-deterministic and unreviewable; it also fails the "no key" rule |
| Schema inference | **Rejected** | One 13-column file: minutes to write the DDL, as long to review generated DDL |
| Header-drift inference | **Rejected** | A rule catches what matters; inference pays only with many sources |
| Resolution-note parser agent | **Rejected** | 11 patterns match all 8,705 notes |
| Duplicate adjudication agent | **Rejected** | Ten-field ranking with weak matches flagged leaves nothing to judge |
| Gold-metric agent | **Rejected** | It would design on top of timestamps that are random here |
| Ontology, RDF, SPARQL | **Rejected** | Spellings to normalise, not meaning to share across sources — below |
| dbt, Airflow, an agent framework | **Rejected at this size** | Three linear stages and single-purpose calls; revisit at production scale |
| Columnar warehouse, partitioning, scheduler | **Deferred** | See "At 100 times the volume" |

### Why not an ontology, RDF and SPARQL

A semantic-web route was the third shape on the table: model the ticket vocabulary as an OWL ontology, load each
ticket as RDF triples, and use SPARQL for the cleaning checks and gold. It loses to a mapping table here for
four reasons:

- **The ambiguity is in spelling, not meaning.** 62 spellings of 10 categories is a closed list with one right
  answer per value, which is a lookup. RDF earns its keep when sources disagree about what a thing *is*; there
  is one source here, so there is nothing to align.
- **There is nothing to reason over.** OWL pays for itself with subsumption and inference (a leak is plumbing,
  plumbing is maintenance, so count it twice). The taxonomy here is flat and gold groups by the same 10 keys, so
  inference would add a model to debug and change no output.
- **It adds a second store and a second language.** The promise of this repository is one PostgreSQL database
  and SQL a reviewer can read line by line before approving it. A triple store beside it means keeping two
  models of the same rows in step, and SPARQL for queries a reviewer would otherwise read as SQL.
- **The gate needs readable artifacts.** Approval is over a value→label mapping, or a `SELECT` with a claimed
  row count. The mapping tables already hold the ontology's useful part — alias, canonical label, evidence,
  approver — one row per decision.

What would change the verdict: several systems with different schemas that must agree on what a category or a
building is, joins across sources by entity, or queries whose shape is not known in advance. Then a shared
vocabulary pays, and the mapping tables would be exported to one.
