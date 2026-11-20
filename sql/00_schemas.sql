-- 00_schemas.sql
-- Idempotent DDL for all four schemas. Safe to run at the start of every pipeline run.
--   bronze : the file as delivered, plus lineage metadata
--   silver : typed, standardised, flagged tickets; quarantine; mapping tables
--   gold   : business aggregates
--   ops    : run log, data-quality results, lineage, agent proposals and calls

CREATE SCHEMA IF NOT EXISTS bronze;
CREATE SCHEMA IF NOT EXISTS silver;
CREATE SCHEMA IF NOT EXISTS gold;
CREATE SCHEMA IF NOT EXISTS ops;

-- ---------------------------------------------------------------------------------------
-- ops: what happened, when, with which rules
-- ---------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS ops.pipeline_run (
    run_id           bigserial PRIMARY KEY,
    command          text        NOT NULL,
    started_at       timestamptz NOT NULL DEFAULT now(),
    finished_at      timestamptz,
    status           text        NOT NULL DEFAULT 'running',   -- running | succeeded | failed
    rule_set_version text,
    message          text
);

CREATE TABLE IF NOT EXISTS ops.stage_log (
    run_id        bigint      NOT NULL REFERENCES ops.pipeline_run,
    stage         text        NOT NULL,
    started_at    timestamptz NOT NULL,
    finished_at   timestamptz NOT NULL,
    rows_in       bigint,
    rows_out      bigint,
    rows_rejected bigint,
    status        text        NOT NULL,
    message       text
);

CREATE TABLE IF NOT EXISTS ops.file_manifest (
    file_sha256 text PRIMARY KEY,
    source_file text        NOT NULL,
    row_count   bigint      NOT NULL,
    batch_id    bigint      NOT NULL,
    loaded_at   timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ops.schema_registry (
    source        text PRIMARY KEY,
    columns       text[]      NOT NULL,
    registered_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ops.drift_event (
    id          bigserial PRIMARY KEY,
    run_id      bigint,
    source_file text,
    kind        text        NOT NULL,      -- header_changed | single_reason_rejects | unmapped_values
    detail      jsonb       NOT NULL,
    detected_at timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ops.dq_results (
    run_id        bigint  NOT NULL,
    rule_id       text    NOT NULL,
    title         text    NOT NULL,
    rows_checked  bigint  NOT NULL,
    rows_affected bigint  NOT NULL,
    pct           numeric(7, 3),
    PRIMARY KEY (run_id, rule_id)
);

CREATE TABLE IF NOT EXISTS ops.lineage_edge (
    run_id           bigint      NOT NULL,
    source_object    text        NOT NULL,
    target_object    text        NOT NULL,
    transformation   text        NOT NULL,
    rule_set_version text,
    executed_by      text        NOT NULL DEFAULT current_user,
    executed_at      timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ops.column_tags (
    source        text NOT NULL,
    column_name   text NOT NULL,
    semantic_type text NOT NULL,
    sensitivity   text NOT NULL,
    basis         text NOT NULL,
    tagged_at     timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (source, column_name)
);

CREATE TABLE IF NOT EXISTS ops.alert (
    id        bigserial PRIMARY KEY,
    run_id    bigint      NOT NULL,
    signal    text        NOT NULL,
    level     text        NOT NULL,        -- warn | fail
    value     numeric,
    threshold numeric,
    message   text        NOT NULL,
    raised_at timestamptz NOT NULL DEFAULT now()
);

-- Every model call, keyed by a hash of provider, model, prompt version and input. Doubles as the response cache.
CREATE TABLE IF NOT EXISTS ops.agent_call (
    cache_key      text PRIMARY KEY,
    task           text        NOT NULL,
    provider       text        NOT NULL,
    model          text        NOT NULL,
    prompt_version text        NOT NULL,
    request        jsonb       NOT NULL,
    response       jsonb       NOT NULL,
    input_tokens   integer     NOT NULL DEFAULT 0,
    output_tokens  integer     NOT NULL DEFAULT 0,
    latency_ms     integer     NOT NULL DEFAULT 0,
    created_at     timestamptz NOT NULL DEFAULT now()
);

-- Data Quality Agent proposals and their validation outcome.
CREATE TABLE IF NOT EXISTS ops.rule_proposal (
    id             bigserial PRIMARY KEY,
    agent_run      text        NOT NULL,
    rule_id        text        NOT NULL,
    column_names   text[]      NOT NULL,
    finding        text        NOT NULL,
    why_it_matters text        NOT NULL,
    action         text        NOT NULL,   -- standardise | set_null | flag | quarantine | deduplicate
    sql_kind       text        NOT NULL,   -- row_predicate | count_query
    sql            text        NOT NULL,
    risk           text        NOT NULL,   -- low | high (as judged by the agent)
    expected_rows  bigint,
    measured_rows  bigint,
    validation     text        NOT NULL,   -- validated | rejected_unsafe_sql | rejected_sql_error | rejected_count_mismatch
    validation_msg text,
    status         text        NOT NULL,   -- proposed | approved | rejected
    model          text,
    prompt_version text,
    reviewed_by    text,
    reviewed_at    timestamptz,
    created_at     timestamptz NOT NULL DEFAULT now()
);

CREATE TABLE IF NOT EXISTS ops.agent_eval (
    eval_run       text        NOT NULL,
    task           text        NOT NULL,
    metric         text        NOT NULL,
    value          numeric,
    detail         jsonb,
    model          text,
    prompt_version text,
    created_at     timestamptz NOT NULL DEFAULT now()
);

-- ---------------------------------------------------------------------------------------
-- bronze: exactly what the file contains. Every source column is text.
-- ---------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS bronze.tickets_raw (
    ticket_id          text,
    created_at         text,
    resolved_at        text,
    category           text,
    priority           text,
    status             text,
    building           text,
    description        text,
    submitted_by       text,
    assigned_to        text,
    resolution_notes   text,
    cost               text,
    sla_hours          text,
    _source_file       text        NOT NULL,
    _file_sha256       text        NOT NULL,
    _source_row_number integer     NOT NULL,
    _row_hash          text        NOT NULL,   -- sha256 over the 13 raw fields
    _dup_seq           integer     NOT NULL,   -- nth occurrence of identical content within one file
    _batch_id          bigint      NOT NULL,   -- ops.pipeline_run.run_id of the load
    _ingested_at       timestamptz NOT NULL DEFAULT now(),
    UNIQUE (_row_hash, _dup_seq)
);

-- ---------------------------------------------------------------------------------------
-- silver: mapping tables. Each row is a reviewed decision with provenance.
--   status: approved (a person signed it off) | provisional (auto-applied by policy, awaiting review)
--           | proposed (held, not applied) | rejected
-- ---------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS silver.category_map (
    raw_key        text PRIMARY KEY,           -- lower-cased, trimmed source value
    category_std   text        NOT NULL,
    status         text        NOT NULL CHECK (status IN ('approved', 'provisional', 'proposed', 'rejected')),
    source         text        NOT NULL,       -- seed | agent | human
    confidence     numeric(4, 3),
    reason         text,
    model          text,
    prompt_version text,
    proposed_at    timestamptz NOT NULL DEFAULT now(),
    approved_by    text,
    approved_at    timestamptz
);

CREATE TABLE IF NOT EXISTS silver.priority_map (
    raw_key        text PRIMARY KEY,
    priority_std   text        NOT NULL,
    status         text        NOT NULL CHECK (status IN ('approved', 'provisional', 'proposed', 'rejected')),
    source         text        NOT NULL,
    confidence     numeric(4, 3),
    reason         text,
    model          text,
    prompt_version text,
    proposed_at    timestamptz NOT NULL DEFAULT now(),
    approved_by    text,
    approved_at    timestamptz
);

CREATE TABLE IF NOT EXISTS silver.person_map (
    raw_key        text PRIMARY KEY,
    person_id      text        NOT NULL,
    person_name    text        NOT NULL,
    status         text        NOT NULL CHECK (status IN ('approved', 'provisional', 'proposed', 'rejected')),
    source         text        NOT NULL,
    confidence     numeric(4, 3),
    reason         text,
    model          text,
    prompt_version text,
    proposed_at    timestamptz NOT NULL DEFAULT now(),
    approved_by    text,
    approved_at    timestamptz
);

CREATE TABLE IF NOT EXISTS silver.issue_map (
    raw_key        text PRIMARY KEY,           -- masked description template
    issue_type     text        NOT NULL,
    hazard_signal  text        NOT NULL CHECK (hazard_signal IN ('safety_hazard', 'service_outage', 'routine')),
    status         text        NOT NULL CHECK (status IN ('approved', 'provisional', 'proposed', 'rejected')),
    source         text        NOT NULL,
    confidence     numeric(4, 3),
    reason         text,
    model          text,
    prompt_version text,
    proposed_at    timestamptz NOT NULL DEFAULT now(),
    approved_by    text,
    approved_at    timestamptz
);

-- ---------------------------------------------------------------------------------------
-- silver: helper functions
-- ---------------------------------------------------------------------------------------
-- Trim, collapse repeated whitespace, and turn an empty string into NULL.
CREATE OR REPLACE FUNCTION silver.norm_text(v text) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
    SELECT nullif(btrim(regexp_replace(v, '\s+', ' ', 'g')), '')
$$;

-- Lookup key for the mapping tables: norm_text, lower-cased. Never NULL.
CREATE OR REPLACE FUNCTION silver.norm_key(v text) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
    SELECT lower(coalesce(silver.norm_text(v), ''))
$$;

-- Which of the eight registered timestamp formats a value has. NULL means "none of them".
-- Explicit patterns on purpose: a new format must fail loudly, not be guessed at.
CREATE OR REPLACE FUNCTION silver.ts_format(v text) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN v ~ '^\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}$'      THEN 'iso_t'
        WHEN v ~ '^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$'      THEN 'iso_space'
        WHEN v ~ '^\d{4}-\d{2}-\d{2}$'                        THEN 'iso_date'
        WHEN v ~ '^\d{2}/\d{2}/\d{4} \d{2}:\d{2} [AP]M$'      THEN 'us_12h'
        WHEN v ~ '^\d{2}/\d{2}/\d{4}$'                        THEN 'us_date'
        WHEN v ~ '^\d{2}-[A-Za-z]{3}-\d{4} \d{2}:\d{2}$'      THEN 'dd_mon'
        WHEN v ~ '^\d{2}-\d{2}-\d{4} \d{2}:\d{2}:\d{2}$'      THEN 'us_dash'
        WHEN v ~ '^\d{10}$'                                   THEN 'epoch_s'
    END
$$;

CREATE OR REPLACE FUNCTION silver.ts_precision(fmt text) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
    SELECT CASE
        WHEN fmt IN ('iso_date', 'us_date') THEN 'day'
        WHEN fmt IN ('us_12h', 'dd_mon')    THEN 'minute'
        WHEN fmt IS NOT NULL                THEN 'second'
    END
$$;

-- Parse a value in one of the registered formats. The US formats are month-first (verified on the data:
-- the first component never exceeds 12). Naive values are kept as written, in source local time.
-- Epoch seconds are an instant, converted to wall-clock time in the source time zone (default UTC).
-- Returns NULL for an unregistered format or an impossible date. to_timestamp() is lenient about zeros
-- (00/00/0000 becomes 1 BC), so the result must format back to the input before it is accepted.
CREATE OR REPLACE FUNCTION silver.parse_ts(v text, source_tz text DEFAULT 'UTC') RETURNS timestamp
LANGUAGE plpgsql STABLE AS $$
DECLARE
    fmt     text := silver.ts_format(v);
    pattern text;
    ts      timestamp;
BEGIN
    IF fmt IS NULL THEN
        RETURN NULL;
    ELSIF fmt = 'epoch_s' THEN
        RETURN to_timestamp(v::bigint) AT TIME ZONE source_tz;
    END IF;
    pattern := CASE fmt
        WHEN 'iso_t'     THEN 'YYYY-MM-DD"T"HH24:MI:SS'
        WHEN 'iso_space' THEN 'YYYY-MM-DD HH24:MI:SS'
        WHEN 'iso_date'  THEN 'YYYY-MM-DD'
        WHEN 'us_12h'    THEN 'MM/DD/YYYY HH12:MI AM'
        WHEN 'us_date'   THEN 'MM/DD/YYYY'
        WHEN 'dd_mon'    THEN 'DD-Mon-YYYY HH24:MI'
        WHEN 'us_dash'   THEN 'MM-DD-YYYY HH24:MI:SS'
    END;
    ts := to_timestamp(v, pattern)::timestamp;
    IF lower(to_char(ts, pattern)) IS DISTINCT FROM lower(v) THEN
        RETURN NULL;
    END IF;
    RETURN ts;
EXCEPTION WHEN others THEN
    RETURN NULL;
END;
$$;

-- Replace asset IDs, the ticket's own building name and all numbers with placeholders.
-- 4,600 distinct descriptions collapse to a few dozen templates, which is what the classifier sees.
CREATE OR REPLACE FUNCTION silver.mask_description(description text, building text) RETURNS text
LANGUAGE sql IMMUTABLE AS $$
    SELECT regexp_replace(
               replace(regexp_replace(description, '\m[A-Z]-\d{3}\M', '<ASSET>', 'g'),
                       coalesce(nullif(building, ''), chr(0x1f)), '<BLDG>'),
               '\d+(st|nd|rd|th)?', '<N>', 'g')
$$;

-- ---------------------------------------------------------------------------------------
-- silver: bronze with trimmed text, parsed timestamps and the hard validity checks (rule R01).
-- One definition, used by the silver build, the profiler and the agents.
-- ---------------------------------------------------------------------------------------
DROP VIEW IF EXISTS silver.v_bronze_checked;
CREATE VIEW silver.v_bronze_checked AS
WITH typed AS (
    SELECT b._row_hash, b._dup_seq, b._batch_id, b._source_row_number,
           silver.norm_text(b.ticket_id)        AS ticket_id,
           silver.ts_format(btrim(b.created_at))  AS created_fmt,
           silver.parse_ts(btrim(b.created_at), coalesce(nullif(current_setting('app.source_tz', true), ''), 'UTC'))  AS created_at,
           silver.ts_format(btrim(b.resolved_at)) AS resolved_fmt,
           silver.parse_ts(btrim(b.resolved_at), coalesce(nullif(current_setting('app.source_tz', true), ''), 'UTC')) AS resolved_at,
           silver.norm_text(b.resolved_at)      AS resolved_txt,
           silver.norm_text(b.category)         AS category_txt,
           length(b.category)                   AS category_raw_len,
           silver.norm_text(b.priority)         AS priority_txt,
           silver.norm_text(b.status)           AS status_txt,
           silver.norm_text(b.building)         AS building_txt,
           silver.norm_text(b.description)      AS description_txt,
           silver.norm_text(b.submitted_by)     AS submitted_by_txt,
           silver.norm_text(b.assigned_to)      AS assigned_to_txt,
           silver.norm_text(b.resolution_notes) AS notes_txt,
           coalesce(btrim(b.cost), '')          AS cost_txt,
           coalesce(btrim(b.sla_hours), '')     AS sla_txt
    FROM bronze.tickets_raw b
)
SELECT t.*,
       array_remove(ARRAY[
           CASE WHEN t.ticket_id IS NULL OR t.ticket_id !~ '^TKT-\d+$' THEN 'invalid_ticket_id' END,
           CASE WHEN t.created_at IS NULL THEN 'unparseable_created_at' END,
           CASE WHEN t.status_txt IS NULL
                  OR t.status_txt NOT IN ('Open', 'In Progress', 'Pending Vendor', 'Escalated', 'Resolved', 'Closed')
                THEN 'invalid_status' END,
           CASE WHEN t.building_txt IS NULL
                  OR lower(t.building_txt) IN ('???', 'unknown', 'n/a', 'null', 'none', 'tbd', 'test', 'test building')
                THEN 'invalid_building' END
       ], NULL) AS reject_reasons
FROM typed t;

-- ---------------------------------------------------------------------------------------
-- silver: target tables
-- ---------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS silver.tickets (
    ticket_id             text PRIMARY KEY,
    ticket_number         integer        NOT NULL,
    created_at            timestamp      NOT NULL,
    created_at_precision  text           NOT NULL,     -- second | minute | day
    created_at_format     text           NOT NULL,
    resolved_at           timestamp,
    resolved_at_precision text,
    resolution_hours      numeric(12, 2),              -- only when resolved_at is not before created_at
    category_std          text           NOT NULL,     -- 'Unclassified' when no approved mapping exists
    category_raw          text,
    priority_std          text,
    priority_raw          text,
    status                text           NOT NULL,
    is_terminal           boolean        NOT NULL,
    building              text           NOT NULL,
    description           text,
    description_template  text,
    issue_type            text,
    hazard_signal         text,
    floor_no              integer,
    location_ref          text,
    asset_id              text,
    submitter_id          text,
    submitter_name        text,
    submitted_by_raw      text,
    assigned_to           text,
    resolution_notes      text,
    note_type             text,
    root_cause            text,
    part_replaced         text,
    hours_on_site         numeric(6, 1),
    parts_eta_days        integer,
    scheduled_weekday     text,
    resolved_by_note      text,
    duplicate_of_ref      text,
    cost_amount           numeric(12, 2),
    cost_status           text           NOT NULL,     -- known | not_applicable | pending | missing | unknown
    sla_hours             integer,
    sla_status            text           NOT NULL,     -- set | not_set | placeholder
    is_duplicate          boolean        NOT NULL,
    duplicate_of          text,
    dup_match_strength    text,                        -- strong | weak (sparse fields only) | truncated_prefix
    dq_flags              text[]         NOT NULL,
    _row_hash             text           NOT NULL,
    _batch_id             bigint         NOT NULL,
    _source_row_number    integer        NOT NULL,
    _run_id               bigint         NOT NULL,
    _rule_set_version     text           NOT NULL
);

CREATE TABLE IF NOT EXISTS silver.tickets_rejected (
    _row_hash          text    NOT NULL,
    _dup_seq           integer NOT NULL,
    _batch_id          bigint  NOT NULL,
    _source_row_number integer NOT NULL,
    reasons            text[]  NOT NULL,
    raw                jsonb   NOT NULL,
    _run_id            bigint  NOT NULL,
    PRIMARY KEY (_row_hash, _dup_seq)
);

-- What gold reads: one row per ticket, duplicates removed.
CREATE OR REPLACE VIEW silver.tickets_current AS
SELECT * FROM silver.tickets WHERE NOT is_duplicate;

-- ---------------------------------------------------------------------------------------
-- gold: business aggregates, rebuilt in full on every run
-- ---------------------------------------------------------------------------------------
CREATE TABLE IF NOT EXISTS gold.sla_performance_monthly (
    month                   date    NOT NULL,
    category                text    NOT NULL,
    priority                text    NOT NULL,
    tickets_opened          integer NOT NULL,
    tickets_valid_interval  integer NOT NULL,
    tickets_measurable      integer NOT NULL,          -- valid interval and a real SLA
    tickets_within_sla      integer NOT NULL,
    median_resolution_hours numeric(12, 2),
    p90_resolution_hours    numeric(12, 2),
    pct_within_sla          numeric(6, 2),             -- of measurable tickets
    pct_measurable          numeric(6, 2),             -- coverage: read the rate with this beside it
    _run_id                 bigint  NOT NULL,
    _built_at               timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (month, category, priority)
);

CREATE TABLE IF NOT EXISTS gold.building_category_hotspots (
    building                     text    NOT NULL,
    category                     text    NOT NULL,
    tickets                      integer NOT NULL,
    open_backlog                 integer NOT NULL,
    hazard_tickets               integer NOT NULL,
    hazard_low_or_no_priority    integer NOT NULL,
    pct_of_building              numeric(6, 2) NOT NULL,
    rank_in_building             integer NOT NULL,
    _run_id                      bigint  NOT NULL,
    _built_at                    timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (building, category)
);

CREATE TABLE IF NOT EXISTS gold.assignee_workload_cost (
    assignee          text    NOT NULL,
    month             date    NOT NULL,
    tickets           integer NOT NULL,
    open_backlog      integer NOT NULL,
    tickets_with_cost integer NOT NULL,
    cost_total        numeric(14, 2),
    cost_median       numeric(12, 2),
    pct_cost_known    numeric(6, 2) NOT NULL,          -- coverage
    _run_id           bigint  NOT NULL,
    _built_at         timestamptz NOT NULL DEFAULT now(),
    PRIMARY KEY (assignee, month)
);

-- Monthly variant of the hotspot model, as a view (cheap, and always consistent with silver).
CREATE OR REPLACE VIEW gold.building_category_monthly AS
SELECT building,
       category_std AS category,
       date_trunc('month', created_at)::date AS month,
       count(*) AS tickets,
       count(*) FILTER (WHERE NOT is_terminal) AS open_backlog
FROM silver.tickets_current
GROUP BY 1, 2, 3;

-- ---------------------------------------------------------------------------------------
-- A read-only role for agent-written SQL. Even if the text guard misses something, this role
-- can only SELECT from bronze.
-- ---------------------------------------------------------------------------------------
DO $$
BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'agent_ro') THEN
        CREATE ROLE agent_ro NOLOGIN;
    END IF;
    BEGIN
        EXECUTE format('GRANT agent_ro TO %I WITH SET TRUE', current_user);
    EXCEPTION WHEN others THEN
        NULL;   -- superusers can SET ROLE without membership
    END;
END $$;
GRANT USAGE ON SCHEMA bronze TO agent_ro;
GRANT SELECT ON bronze.tickets_raw TO agent_ro;
