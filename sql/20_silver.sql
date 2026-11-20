-- 20_silver.sql
-- Rebuilds silver from bronze using the approved mapping tables. The pipeline runs this file inside one
-- transaction, so a failure leaves the previous version of silver in place.
-- Settings supplied by the pipeline: app.run_id, app.rule_set_version, app.source_tz.
-- Rule IDs (R01..R17) refer to rules/cleaning_rules.yaml, where each rule's reason is documented.

CREATE TEMP TABLE _checked ON COMMIT DROP AS
SELECT * FROM silver.v_bronze_checked;

-- R01: quarantine rows that fail a hard check. They stay queryable, with every reason listed.
TRUNCATE silver.tickets_rejected;
INSERT INTO silver.tickets_rejected (_row_hash, _dup_seq, _batch_id, _source_row_number, reasons, raw, _run_id)
SELECT c._row_hash, c._dup_seq, c._batch_id, c._source_row_number, c.reject_reasons,
       to_jsonb(b) - '_ingested_at', current_setting('app.run_id')::bigint
FROM _checked c
JOIN bronze.tickets_raw b USING (_row_hash, _dup_seq)
WHERE cardinality(c.reject_reasons) > 0;

-- One row per ticket_id. If a later delivery repeats a ticket, the latest delivery wins.
CREATE TEMP TABLE _valid ON COMMIT DROP AS
SELECT DISTINCT ON (ticket_id) *
FROM _checked
WHERE cardinality(reject_reasons) = 0
ORDER BY ticket_id, _batch_id DESC, _source_row_number DESC;

-- R02: repair swapped category / description. A row is swapped when its category is not a known
-- label and its description is. R08: map category to a standard value.
CREATE TEMP TABLE _repaired ON COMMIT DROP AS
SELECT v.*,
       (cm.raw_key IS NULL AND dm.raw_key IS NOT NULL)                                         AS is_swapped,
       CASE WHEN cm.raw_key IS NULL AND dm.raw_key IS NOT NULL THEN v.description_txt ELSE v.category_txt END    AS category_fixed,
       CASE WHEN cm.raw_key IS NULL AND dm.raw_key IS NOT NULL THEN v.category_txt    ELSE v.description_txt END AS description_fixed,
       coalesce(cm.category_std, dm.category_std)                                              AS category_std
FROM _valid v
LEFT JOIN silver.category_map cm ON cm.raw_key = silver.norm_key(v.category_txt)    AND cm.status IN ('approved', 'provisional')
LEFT JOIN silver.category_map dm ON dm.raw_key = silver.norm_key(v.description_txt) AND dm.status IN ('approved', 'provisional');

-- R03: duplicates are rows identical on ten business fields (everything except ticket_id, created_at
-- and submitted_by), compared after the swap repair. The lowest ticket number survives.
CREATE TEMP TABLE _ranked ON COMMIT DROP AS
SELECT r.*,
       substring(r.ticket_id FROM '\d+')::integer AS ticket_number,
       row_number() OVER w AS dup_rank,
       first_value(r.ticket_id) OVER w AS dup_first
FROM _repaired r
WINDOW w AS (PARTITION BY r.category_fixed, r.priority_txt, r.status_txt, r.building_txt, r.description_fixed,
                          r.resolved_txt, r.assigned_to_txt, r.notes_txt, r.cost_txt, r.sla_txt
             ORDER BY substring(r.ticket_id FROM '\d+')::integer);

-- R03b: a swapped row lost its description beyond 60 characters, so it cannot match its original exactly.
-- It is a duplicate when another ticket matches the other nine fields and its description starts with
-- the truncated text. Finding the longer original is also the proof that the text was truncated.
CREATE TEMP TABLE _truncated ON COMMIT DROP AS
SELECT s.ticket_id, (array_agg(o.ticket_id ORDER BY o.ticket_number))[1] AS original_id
FROM _ranked s
JOIN _ranked o
  ON o.dup_rank = 1 AND NOT o.is_swapped
 AND o.category_fixed = s.category_fixed
 AND o.priority_txt     IS NOT DISTINCT FROM s.priority_txt
 AND o.status_txt = s.status_txt AND o.building_txt = s.building_txt
 AND o.resolved_txt     IS NOT DISTINCT FROM s.resolved_txt
 AND o.assigned_to_txt  IS NOT DISTINCT FROM s.assigned_to_txt
 AND o.notes_txt        IS NOT DISTINCT FROM s.notes_txt
 AND o.cost_txt = s.cost_txt AND o.sla_txt = s.sla_txt
 AND length(o.description_fixed) > length(s.description_fixed)
 AND starts_with(o.description_fixed, s.description_fixed)
WHERE s.is_swapped AND s.dup_rank = 1 AND s.category_raw_len = 60
GROUP BY s.ticket_id;

CREATE TEMP TABLE _deduped ON COMMIT DROP AS
SELECT r.*,
       t.original_id IS NOT NULL                                   AS is_truncated,
       (r.dup_rank > 1 OR t.original_id IS NOT NULL)               AS is_duplicate,
       CASE WHEN t.original_id IS NOT NULL THEN t.original_id      -- the exact-group leader is itself a truncated copy
            WHEN r.dup_rank > 1 THEN r.dup_first END               AS duplicate_of,
       CASE WHEN t.original_id IS NOT NULL THEN 'truncated_prefix'
            WHEN r.dup_rank > 1 AND r.resolved_txt IS NULL AND r.notes_txt IS NULL AND r.cost_txt = '' THEN 'weak'
            WHEN r.dup_rank > 1 THEN 'strong' END                  AS dup_match_strength
FROM _ranked r
LEFT JOIN _truncated t ON t.ticket_id = r.dup_first;

TRUNCATE silver.tickets;
INSERT INTO silver.tickets
WITH enriched AS (
    SELECT d.*,
           -- R06: compare by date when either timestamp has only day precision
           CASE WHEN d.resolved_at IS NULL THEN NULL
                WHEN silver.ts_precision(d.created_fmt) = 'day' OR silver.ts_precision(d.resolved_fmt) = 'day'
                     THEN d.resolved_at::date < d.created_at::date
                ELSE d.resolved_at < d.created_at END                                   AS is_reversed,
           d.status_txt IN ('Resolved', 'Closed')                                        AS is_terminal,
           silver.mask_description(d.description_fixed, d.building_txt)                  AS template,
           -- R11: cost
           CASE WHEN replace(d.cost_txt, '$', '') ~ '^-?\d+(\.\d+)?$' THEN replace(d.cost_txt, '$', '')::numeric END AS cost_num,
           pm.priority_std, pe.person_id, pe.person_name, im.issue_type, im.hazard_signal
    FROM _deduped d
    LEFT JOIN silver.priority_map pm ON pm.raw_key = silver.norm_key(d.priority_txt)     AND pm.status IN ('approved', 'provisional')
    LEFT JOIN silver.person_map   pe ON pe.raw_key = silver.norm_key(d.submitted_by_txt) AND pe.status IN ('approved', 'provisional')
    LEFT JOIN silver.issue_map    im ON im.raw_key = silver.mask_description(d.description_fixed, d.building_txt)
                                    AND im.status IN ('approved', 'provisional')
)
SELECT
    e.ticket_id,
    e.ticket_number,
    e.created_at,                                                   -- R05
    silver.ts_precision(e.created_fmt),
    e.created_fmt,
    e.resolved_at,
    silver.ts_precision(e.resolved_fmt),
    CASE WHEN e.resolved_at IS NOT NULL AND NOT e.is_reversed
         THEN round(greatest(extract(epoch FROM e.resolved_at - e.created_at), 0) / 3600.0, 2) END,   -- R06
    coalesce(e.category_std, 'Unclassified'),                       -- R08
    e.category_fixed,
    e.priority_std,                                                 -- R09: blank stays NULL
    e.priority_txt,
    e.status_txt,
    e.is_terminal,
    e.building_txt,
    e.description_fixed,
    e.template,
    e.issue_type,                                                   -- R14 (classification agent, approved)
    e.hazard_signal,
    coalesce(substring(e.description_fixed FROM '(\d+)(?:st|nd|rd|th)? floor'),
             substring(e.description_fixed FROM 'floor (\d+)'))::integer,
    substring(e.description_fixed FROM '((?:room|desk|spot|cubicle|Door|sector) \d+)'),
    substring(e.description_fixed FROM '\m([A-Z]-\d{3})\M'),
    e.person_id,                                                    -- R10
    e.person_name,
    e.submitted_by_txt,
    e.assigned_to_txt,                                              -- R16: blank is NULL, never backfilled
    e.notes_txt,
    -- R13: resolution notes follow fixed sentence patterns
    CASE WHEN e.notes_txt IS NULL THEN NULL
         WHEN e.notes_txt ~ '^Duplicate of ticket #\d+\. Closing\.$'                      THEN 'duplicate_reference'
         WHEN e.notes_txt ~ '^Resolved\. Root cause: .+\.$'                               THEN 'root_cause'
         WHEN e.notes_txt ~ '^Completed\. Took [\d.]+ hours on site\.$'                   THEN 'completed'
         WHEN e.notes_txt ~ '^Dispatched technician\. Fixed on site\. Replaced .+\.$'     THEN 'part_replaced'
         WHEN e.notes_txt ~ '^Vendor called\. Scheduled for \w+\.$'                       THEN 'vendor_scheduled'
         WHEN e.notes_txt ~ '^Parts on order\. ETA \d+ business days\.$'                  THEN 'parts_on_order'
         WHEN e.notes_txt ~ '^Resolved by .+\.$'                                          THEN 'resolved_by'
         WHEN e.notes_txt = 'Temporary fix applied. Permanent repair scheduled.'          THEN 'temporary_fix'
         WHEN e.notes_txt = 'Issue could not be reproduced. Monitoring.'                  THEN 'not_reproduced'
         WHEN e.notes_txt IN ('fixed', 'done')                                            THEN e.notes_txt
         ELSE 'other' END,
    substring(e.notes_txt FROM '^Resolved\. Root cause: (.+)\.$'),
    substring(e.notes_txt FROM '^Dispatched technician\. Fixed on site\. Replaced (.+)\.$'),
    substring(e.notes_txt FROM '^Completed\. Took ([\d.]+) hours on site\.$')::numeric,
    substring(e.notes_txt FROM '^Parts on order\. ETA (\d+) business days\.$')::integer,
    substring(e.notes_txt FROM '^Vendor called\. Scheduled for (\w+)\.$'),
    substring(e.notes_txt FROM '^Resolved by (.+)\.$'),
    'TKT-' || substring(e.notes_txt FROM '^Duplicate of ticket #(\d+)\. Closing\.$'),
    CASE WHEN e.cost_num >= 0 THEN round(e.cost_num, 2) END,        -- R11
    CASE WHEN e.cost_txt = ''             THEN 'missing'
         WHEN upper(e.cost_txt) = 'N/A'   THEN 'not_applicable'
         WHEN upper(e.cost_txt) = 'TBD'   THEN 'pending'
         WHEN e.cost_num >= 0             THEN 'known'
         ELSE 'unknown' END,
    CASE WHEN e.sla_txt IN ('4', '8', '24', '48', '72') THEN e.sla_txt::integer END,   -- R12
    CASE WHEN e.sla_txt IN ('4', '8', '24', '48', '72') THEN 'set'
         WHEN e.sla_txt = '' OR upper(e.sla_txt) = 'N/A' THEN 'not_set'
         ELSE 'placeholder' END,
    e.is_duplicate,                                                 -- R03, R03b
    e.duplicate_of,
    e.dup_match_strength,
    array_remove(ARRAY[
        CASE WHEN e.is_swapped THEN 'swapped_columns' END,
        CASE WHEN e.is_truncated OR (e.is_swapped AND NOT e.is_duplicate AND e.category_raw_len = 60) THEN 'description_truncated' END,
        CASE WHEN e.category_std IS NULL THEN 'category_unmapped' END,
        CASE WHEN e.priority_txt IS NULL THEN 'priority_missing'
             WHEN e.priority_std IS NULL THEN 'priority_unmapped' END,
        CASE WHEN e.submitted_by_txt IS NOT NULL AND e.person_id IS NULL THEN 'submitter_unmapped' END,
        CASE WHEN e.resolved_txt IS NOT NULL AND e.resolved_at IS NULL THEN 'resolved_at_unparseable' END,
        CASE WHEN e.is_reversed THEN 'resolved_before_created' END,                                     -- R06
        CASE WHEN NOT e.is_terminal AND e.resolved_at IS NOT NULL THEN 'resolved_at_on_open_ticket' END, -- R07
        CASE WHEN e.is_terminal AND e.resolved_at IS NULL THEN 'terminal_without_resolved_at' END,
        CASE WHEN e.cost_txt <> '' AND upper(e.cost_txt) NOT IN ('N/A', 'TBD') AND coalesce(e.cost_num, -1) < 0 THEN 'cost_placeholder' END,
        CASE WHEN e.sla_txt <> '' AND upper(e.sla_txt) <> 'N/A' AND e.sla_txt NOT IN ('4', '8', '24', '48', '72') THEN 'sla_placeholder' END,
        CASE WHEN e.is_duplicate THEN 'duplicate' END
    ], NULL),
    e._row_hash,
    e._batch_id,
    e._source_row_number,
    current_setting('app.run_id')::bigint,
    current_setting('app.rule_set_version')
FROM enriched e;
