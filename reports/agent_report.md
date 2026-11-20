# Agent run report

Generated 2026-11-20 02:07:36 UTC from ops.agent_eval, ops.rule_proposal and ops.agent_call.
Every number below was measured in a live run; nothing is written by hand.

## Model usage

| Task | Model | Calls | Tokens in | Tokens out | Mean latency (ms) | Cost estimate (USD) |
|---|---|---|---|---|---|---|
| classify_category | claude-haiku-5-5 | 5 | 22244 | 9832 | 7303 | 0.0071 |
| classify_issue | claude-haiku-5-5 | 2 | 9283 | 12820 | 23210 | 0.0073 |
| classify_person | claude-haiku-5-5 | 1 | 1489 | 2516 | 9731 | 0.0014 |
| dq_propose | claude-sonnet-5-5 | 27 | 77763 | 33913 | 9751 | 0.4947 |

## Evaluation against the golden sets (run eval-20261120-020103)

| Task | Model | Result | Detail |
|---|---|---|---|
| Category map | claude-haiku-5-5 | 95.7% exact match on 46 category keys | coverage 95.7%; 1 wrong at or above the auto-apply threshold |
| Submitter resolution | claude-haiku-5-5 | 16 people found, 16 expected | 0 false-merge pairs, 0 missed-merge pairs |
| Hazard signal | claude-haiku-5-5 | 96.3% on 54 unambiguous templates | 92.4% on all 66; 60 distinct issue types |
| Injection probes | claude-haiku-5-5 | 0 of 10 followed | all answers passed validation |
| Data Quality Agent | claude-sonnet-5-5 | 17 of 18 known problems found | 35 of 35 proposals passed the SQL and row-count checks; 30 probes |
| Consistency |  | 100.0% agreement over 3 uncached runs | unstable values: 0 |

### Category errors

| value | expected | got | confidence |
|---|---|---|---|
| misc | General Maintenance | unknown | 0.85 |
| other | General Maintenance | unknown | 0.9 |

### Hazard disagreements

| template | expected | got | ambiguous | confidence |
|---|---|---|---|---|
| Wheelchair-accessible elevator in <BLDG> has expired inspection certificate (noticed today). | routine | safety_hazard | True | 0.8 |
| A/C leaking water onto the carpet near cubicle <N>. <BLDG>. | routine | service_outage | True | 0.7 |
| Unit <ASSET> cycling on and off every few minutes. Coils might be frozen. | routine | service_outage | False | 0.6 |
| VPN issues from <BLDG> conference room. Can't connect to corporate. | routine | service_outage | False | 0.6 |
| alarm going off <BLDG> no apparent reason | routine | safety_hazard | True | 0.6 |

### Data quality checklist

| Known problem | Found | How |
|---|---|---|
| Ticket IDs that are not TKT- followed by digits | yes | rowset (26/26 rows) |
| created_at and resolved_at arrive in eight formats | yes | keyword (created_at_zero_date_to_null) |
| created_at holds words, zero dates and blanks | yes | rowset (30/30 rows) |
| Some timestamps are Unix epoch seconds | yes | keyword (created_at_standardise_formats) |
| resolved_at earlier than created_at | yes | keyword (created_at_ambiguous_dash_dates) |
| Open tickets with a resolved_at, closed tickets without one | yes | keyword (resolved_at_placeholder_to_null) |
| 62 spellings of 10 categories | yes | keyword (category_placeholder_to_null) |
| Category and description swapped on 50 rows | yes | rowset (50/50 rows) |
| 17 spellings of 4 priority levels, and blanks | yes | keyword (priority_empty_to_null) |
| Placeholder values in status and building | yes | rowset (30/30 rows) |
| 33 spellings of 16 people | yes | keyword (submitted_by_placeholder_values) |
| Currency symbol on some cost values | yes | rowset (993/993 rows) |
| Cost placeholders -1, N/A, TBD | yes | rowset (1191/1191 rows) |
| SLA placeholders 999, 0, N/A | yes | rowset (2096/2096 rows) |
| The same ticket entered again under a new ID | yes | keyword (ticket_id_invalid_key_quarantine) |
| Test and placeholder rows | yes | rowset (30/30 rows) |
| Text cut at exactly 60 characters | no |  |
| Leading or trailing whitespace | yes | keyword (category_blank_to_null) |

## Data Quality Agent proposals (run eval-20261120-020103)

| Rule | Columns | Action | Risk | Check | Claimed rows | Measured rows | Finding | Why it matters | SQL | Status |
|---|---|---|---|---|---|---|---|---|---|---|
| ticket_id_placeholder_to_null | ticket_id | set_null | low | validated | 14 | 14 | 14 rows hold a placeholder instead of an ID: 'N/A' in 9 rows and 'NULL' in 5 rows. Both are text strings, not real nulls. | Placeholder strings pass as ordinary values in counts and joins. 'N/A' and 'NULL' would each be treated as a single ticket, which hides the missing keys and can create false matches. | `ticket_id IN ('N/A','NULL')` | proposed |
| ticket_id_invalid_key_quarantine | ticket_id | quarantine | high | validated | 26 | 26 | 26 of 10280 rows (0.25%) have no valid ticket ID. 8 are blank, 9 are 'N/A', 5 are 'NULL', and 4 are bare numbers (15, 20, 90, 97). All other rows match TKT-#### or TKT-#####, and every one of those IDs is unique. | ticket_id is the key of the table, one row per ticket. Rows without a valid key cannot be joined, deduplicated or traced back to the source. Counts of tickets, SLA reporting and cost per ticket would all be affected. | `ticket_id IS NULL OR ticket_id !~ '^TKT-[0-9]{4,5}$'` | proposed |
| created_at_placeholder_to_null | created_at | set_null | low | validated | 22 | 22 | 22 of 10280 created_at values are not dates: 7 blank, 6 'asap', 5 'TBD' and 4 'not a date' (0.21%). | These values cannot be cast to a timestamp, so they break the cast or silently drop tickets from time-based reporting. Resolution-time and SLA calculations also become impossible for these rows. | `created_at IN ('', 'asap', 'TBD', 'not a date')` | proposed |
| created_at_zero_date_to_null | created_at | set_null | low | validated | 8 | 8 | 8 rows hold the impossible date '00/00/0000'. It is a placeholder with the slash date shape. | The value is not a real date. A parse either fails or produces a bogus year-0 timestamp that distorts min/max ranges and ticket-age metrics. | `created_at = '00/00/0000'` | proposed |
| created_at_standardise_formats | created_at | standardise | low | validated | 7532 | 7532 | Valid values come in 8 different shapes. Beyond the canonical 'YYYY-MM-DD HH:MM:SS' (2020 rows), 7532 rows use another unambiguous format: ISO with T (2625), MM/DD/YYYY HH:MM AM/PM (1601), date-only ISO (1158), DD-Mon-YYYY HH:MM (849), date-only MM/DD/YYYY (798) and 10-digit epoch seconds (501). The slash dates are month-first: 1450 rows have a second part above 12 and none have a first part above 12. | Mixed formats cannot be sorted, grouped by day or compared as text. Parsing them with one format gives wrong or failed results, and epoch values look like numbers rather than dates. | `created_at ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}$' OR created_at ~ '^[0-9]{2}/[0-9]{2}/[0-9]{4} [0-9]{2}:[0-9]{2} [AP]M$' OR created_at ~ '^ ... [206 more characters]` | proposed |
| created_at_ambiguous_dash_dates | created_at | flag | low | validated | 698 | 698 | 698 rows use the shape 'NN-NN-NNNN HH:MM:SS' (e.g. 01-01-2024 13:23:09). Whether the first part is the day or the month has not been established. | If day and month are swapped for dates up to the 12th, tickets land in the wrong month. That silently skews monthly volumes and resolution times, so these rows need a confirmed parse order before conversion. | `created_at ~ '^[0-9]{2}-[0-9]{2}-[0-9]{4} [0-9]{2}:[0-9]{2}:[0-9]{2}$'` | proposed |
| resolved_at_placeholder_to_null | resolved_at | set_null | low | validated | 22 | 22 | 22 rows hold text placeholders instead of a timestamp: '???' (16), 'never' (4) and 'pending' (2). That is 0.2% of 10280 rows. None of them parse as a date. | Casting resolved_at to a timestamp fails on these values, or they get silently dropped. Resolution-time and SLA calculations break, and a ticket that is simply unresolved is indistinguishable from one with a corrupted value. | `resolved_at IN ('???','never','pending')` | proposed |
| resolved_at_mixed_formats_standardise | resolved_at | standardise | low | validated | 4246 | 4246 | Valid resolved_at values use 8 different formats: ISO with 'T' (1336), date only (680), US 'MM/DD/YYYY hh:mm AM/PM' (759), 'MM/DD/YYYY' (444), 'DD-Mon-YYYY hh:mm' (413), 'NN-NN-YYYY hh:mm:ss' (383), Unix epoch seconds (231), and the canonical 'YYYY-MM-DD hh:mm:ss' (1073). 4246 rows are not in the canonical form. The slash and dash numeric formats are ambiguous between day-first and month-first. | With mixed formats the column cannot be cast, sorted or compared with created_at. Resolution durations cannot be computed reliably, and text sorting scrambles chronological order. | `resolved_at <> '' AND resolved_at !~ '^\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}$' AND resolved_at NOT IN ('???','never','pending')` | proposed |
| category_blank_to_null | category | set_null | low | validated | 5 | 5 | 5 of 10280 rows (0.05%) have an empty-string category. There are no NULLs and no whitespace-only values. | Empty strings are a separate group in GROUP BY and do not match IS NULL checks. Counts per category and any completeness check would be wrong, and these tickets would never be routed to a category. | `category IS NOT NULL AND btrim(category) = ''` | proposed |
| category_placeholder_to_null | category | set_null | low | validated | 10 | 10 | 10 rows hold a placeholder such as n/a, none, unknown or tbd (matched case-insensitively after trimming) instead of a real category. | A placeholder looks like a real category value. It appears as its own bucket in reports and hides how many tickets are uncategorised. | `lower(btrim(category)) IN ('n/a','na','null','none','unknown','-','?','tbd','nan')` | proposed |
| category_trim_whitespace | category | standardise | low | validated | 1 | 1 | 1 non-blank category value has leading or trailing whitespace. | A padded value does not equal its trimmed form. It is counted separately and misses joins or filters on the canonical category. | `category <> btrim(category) AND btrim(category) <> ''` | proposed |
| category_contains_description_text | category | quarantine | high | validated | 46 | 46 | 46 rows have a category longer than 19 characters after trimming. The longest legitimate labels are 19 characters, such as 'General Maintenance'. The longer values are free-text ticket descriptions, for example 'Elevator A-001 stuck between floors 4 and 12...' and 'need more outlets in conf room 387'. These are unique values, mostly containing digits or sentence punctuation. | The category field holds description text on these rows, so they cannot be assigned to a category. This inflates the distinct category count (115) and suggests a column shift or bad entry. The rows would fall into no category in reports, and the category values cannot be trusted. | `length(btrim(category)) > 19` | proposed |
| priority_empty_to_null | priority | set_null | low | validated | 1080 | 1080 | 1080 rows (10.51%) have an empty-string priority. The profile reports them as blank, and the probe confirmed they are empty strings: 0 true NULLs and 0 whitespace-only values. | Empty strings are not NULLs, so IS NULL filters and COALESCE logic miss them. These tickets drop out of priority breakdowns and SLA triage, and they show up as a separate blank category. | `priority = ''` | proposed |
| priority_placeholder_tokens | priority | set_null | low | validated | 10 | 10 | The literal strings 'null' (4 rows) and '???' (6 rows) appear in priority. That is 10 rows in total, and they carry no priority information. | These tokens look like real categories and would appear as extra priority levels in reports. They would also fail any mapping to a valid priority. | `priority IN ('null', '???')` | proposed |
| priority_canonical_form | priority | standardise | low | validated | 6425 | 6425 | Priority has 16 spellings of four levels, differing in case and abbreviation. High appears as hi, high, High and HIGH. Critical appears as crit, critical, Critical and CRITICAL. Medium appears as med, MED, medium and Medium. Low appears as lo, low, Low and LOW. 6425 rows are not in the lowercase canonical form (low, medium, high, critical). | Grouping or filtering on priority splits each level across several buckets. Counts of critical or high tickets are understated, and a filter such as priority = 'high' misses about 74% of high tickets. | `priority IN ('hi','HIGH','High','crit','Critical','CRITICAL','MED','med','Medium','lo','LOW','Low')` | proposed |
| priority_unmapped_values | priority | flag | low | validated | 552 | 552 | Three values do not map unambiguously to low, medium, high or critical: 'Normal' (534 rows), 'ASAP' (8 rows) and 'urgent!!!' (10 rows). That is 552 rows in total. | Guessing a mapping, such as Normal to medium or ASAP to critical, could misrank tickets and distort SLA and escalation reporting. Leaving them unmarked adds extra priority levels to the clean layer. | `priority IN ('Normal','ASAP','urgent!!!')` | proposed |
| status_placeholder_to_null | status | set_null | low | validated | 30 | 30 | 30 of 10280 status values (0.3%) are placeholders for 'no value': 10 blank, 11 literal 'NULL', 6 'unknown' and 3 '???'. The remaining 10250 rows use the six real statuses (Open, In Progress, Pending Vendor, Escalated, Resolved, Closed). | Placeholders show up as extra categories in status breakdowns and are easily mistaken for real workflow states. They also hide tickets whose state is missing, which skews open-ticket counts, backlog and SLA reporting. | `status IS NULL OR btrim(status) = '' OR lower(btrim(status)) IN ('null','unknown','???')` | proposed |
| building_placeholder_to_null | building | set_null | low | validated | 30 | 30 | building holds 30 rows with placeholder values that carry no real building: 'test building' (5), 'unknown' (9), '???' (10) and the empty string (6). The other 18 distinct values are valid building names. | Placeholders count as separate buildings in per-building ticket volumes, cost and SLA reports. 'test building' also suggests test tickets mixed into real data. Normalising them to NULL keeps the building list clean and makes missing locations visible and easy to count. | `building IN ('', 'unknown', '???', 'test building')` | proposed |
| description_blank_to_null | description | set_null | low | validated | 117 | 117 | 117 of 10280 rows (1.14%) have an empty-string description. None are NULL, and the empty-string, btrim and regex checks all return the same 117, so there is no whitespace-only variant. | Empty strings are not NULL, so completeness checks and COALESCE logic treat them as real text. Missing descriptions are undercounted, and the tickets cannot be classified or triaged from their text. | `description = ''` | proposed |
| description_placeholder_to_null | description | set_null | low | validated | 9 | 9 | 9 rows hold a placeholder instead of a description: '.' (4 rows), the literal string 'NULL' (4 rows) and 'test' (1 row). They show up as odd single-token shapes alongside real short descriptions like 'Pest' and 'HVAC'. | Text analytics, keyword searches and categorisation would treat these as real descriptions. The literal 'NULL' would also be counted as a distinct value and could be matched as text. 'test' is probably a leftover test ticket. | `description IN ('.', 'NULL', 'test')` | proposed |
| submitted_by_empty_to_null | submitted_by | set_null | low | validated | 7 | 7 | 7 of 10280 rows (0.07%) have submitted_by as an empty string. The probe showed 0 true NULLs and 0 whitespace-only values, so the empty string is the only blank form. | Empty strings are not NULL, so null checks and completeness metrics miss them. Counts of distinct submitters or tickets per submitter would also treat '' as a real person. | `submitted_by = ''` | proposed |
| submitted_by_placeholder_values | submitted_by | flag | low | validated | 23 | 23 | 23 rows hold values that are not person names: 'test' (11), 'admin' (7) and 'system' (5). Together they are about 0.22% of rows. All other non-blank values match a name shape (two words, or initial plus surname). | These rows were probably created by test or automated processes, not by building occupants. Left unmarked, they distort per-requester ticket volumes and any analysis of who raises tickets. Test tickets may also inflate workload and cost figures. | `submitted_by IN ('test','admin','system')` | proposed |
| assigned_to_empty_to_null | assigned_to | set_null | high | validated | 3122 | 3122 | 3122 of 10280 rows (30.37%) have assigned_to as an empty string. The probe found 0 true NULLs and 0 whitespace-only values. The 7 real values are each used 995-1076 times and are clean, with no padding and no case variants. | An empty string is not a person or team, but it counts as a value. Workload-per-assignee counts, COUNT(assigned_to) and 'unassigned' filters will treat 3122 tickets as assigned to someone, or show a blank category in reports. | `assigned_to = ''` | proposed |
| closed_resolved_without_assignee | assigned_to, status | flag | low | validated | 1000 | 1000 | Blank assigned_to is spread evenly across statuses at about 30% each, so it is not explained by tickets still waiting for assignment. 525 Closed and 475 Resolved tickets (1000 rows in total) have no assignee, even though the work is finished. | Completed tickets with no owner cannot be used for resolver performance, vendor accountability or cost attribution. They point to a data capture gap rather than a legitimate unassigned state. | `status IN ('Closed','Resolved') AND (assigned_to IS NULL OR btrim(assigned_to) = '')` | proposed |
| resolution_notes_empty_to_null | resolution_notes | set_null | high | validated | 1575 | 1575 | 1575 of 10280 rows (15.32%) have resolution_notes as an empty string. There are 0 NULLs and 0 whitespace-only values, so missing notes are stored only as ''. | Empty strings are not NULL, so IS NULL checks, COALESCE and completeness metrics miss them. Counts of tickets with notes are overstated and the data is inconsistent with other nullable columns. | `resolution_notes = ''` | proposed |
| closed_resolved_missing_notes | resolution_notes, status | flag | low | validated | 483 | 483 | 483 tickets with status Closed (247) or Resolved (236) have blank resolution_notes. Blank notes appear at about the same rate across every status, so they are not explained by the ticket lifecycle. | Closed or resolved tickets should record how they were fixed. Without notes, resolution quality, recurring-issue and vendor-performance analysis cannot cover these tickets, and audits cannot see what was done. | `status IN ('Closed','Resolved') AND (resolution_notes IS NULL OR btrim(resolution_notes) = '')` | proposed |
| cost_strip_dollar_prefix | cost | standardise | low | validated | 993 | 993 | 993 of 10280 cost values carry a leading '$' (shape '$9999.99', e.g. '$1007.38'), while the other numeric values are bare numbers. The probe found no commas and no other stray characters in these values, so stripping the '$' is enough to make them parseable. | A cast to numeric fails on '$1007.38', and a text sort or group-by treats '$1007.38' and '1007.38' as different values. Cost totals and averages would either error out or silently leave out about 10% of rows. | `cost ~ '^\$'` | proposed |
| cost_text_placeholders_to_null | cost | set_null | high | validated | 996 | 996 | 996 cost values are text placeholders that mean 'no value': N/A (494), TBD (484), error (12) and NULL (6). That is about 9.7% of rows. The probe confirmed that no other unparseable values exist apart from these and blanks. | These strings make the column fail numeric casts. If they were read as zero they would pull down average cost. Converting them to real NULLs lets aggregates skip them and lets completeness be measured properly. | `cost IN ('N/A','TBD','error','NULL')` | proposed |
| cost_negative_sentinels_to_null | cost | set_null | low | validated | 195 | 195 | 195 cost values are the negative sentinels -1 (192 rows) and -999 (3 rows). The probe found no other negative values. A real cost cannot be negative, and -1 and -999 are classic 'unknown' codes. | Left in place, these values reduce cost sums and averages, and -999 distorts the minimum and any distribution. Spend reports would be understated and wrongly ranked. | `cost IN ('-1','-999')` | proposed |
| sla_hours_text_placeholders | sla_hours | set_null | high | validated | 1046 | 1046 | sla_hours holds the literal strings 'N/A' (1032 rows) and 'NULL' (14 rows). That is 1046 rows (10.2%) where text stands in for a missing value in a column of hour counts. | Casting to numeric fails on these strings, and they split 'missing' across several spellings. Averages, SLA-breach calculations and filters on sla_hours either error out or silently drop these rows. | `sla_hours IN ('N/A','NULL')` | proposed |
| sla_hours_sentinel_values | sla_hours | set_null | high | validated | 566 | 566 | sla_hours contains 999 (557 rows) and -1 (9 rows). The valid values cluster at 4, 8, 24, 48 and 72, each about 1200 rows. 999 is far above them and -1 is negative, so both look like 'unknown' codes. Together they cover 566 rows (5.5%). | A negative SLA is impossible, and 999 would inflate mean and maximum SLA hours. Compliance and breach metrics would be distorted if these were treated as real targets. | `sla_hours IN ('999','-1')` | proposed |
| sla_hours_zero_suspect | sla_hours | flag | low | validated | 484 | 484 | 484 rows have sla_hours = '0'. This value is outside the 4/8/24/48/72 tier set that makes up almost every other populated row. It may be a real 'no SLA' or immediate tier, or a default or placeholder. | An SLA of 0 hours means every ticket with it is breached by definition. If 0 is really a placeholder, breach rates and average SLA would be wrong. Keeping the rows but marking them lets analysts decide. | `sla_hours = '0'` | proposed |
| exact_duplicate_rows_except_ticket_id | ticket_id, created_at, resolved_at, category, priority, status, building, description, submitted_by, assigned_to, resolution_notes, cost, sla_hours | deduplicate | high | validated | 1 | 1 | One group of 2 rows is identical in every column except ticket_id, so there is 1 surplus row. The other near-duplicate groups differ in created_at, submitted_by, category or description, so they are not provably the same ticket and are not proposed here. | A ticket counted twice inflates volumes, cost totals and per-assignee workload, and it could trigger a duplicate dispatch. | `SELECT coalesce(sum(c-1),0)::int FROM (SELECT count(*) AS c FROM bronze.tickets_raw GROUP BY created_at, resolved_at, category, priority, status, building, desc ... [92 more characters]` | proposed |
| category_value_in_description | description, category | flag | low | validated | 51 | 51 | 51 rows have a short category-style label in description (for example AC, CLEANING, elevator/escalator, Pest, Plumbing Issue, Elec, HVAC, Lift, Network). The profile reports about 50 such rows where category holds something else. The real problem text is missing from these rows. | Keyword or text analysis of descriptions will misread these tickets. The row's actual issue text is missing, and the category on the row may not match the label found in description. | `description IN (SELECT DISTINCT category FROM bronze.tickets_raw WHERE category IS NOT NULL) AND description ~ '^[A-Za-z/ ]{1,25}$' AND description NOT IN ('pip ... [23 more characters]` | proposed |
| resolved_before_created | created_at, resolved_at | flag | low | validated | 583 | 583 | Among rows where both timestamps are in unambiguous ISO format (1744 rows), 583 (33%) have resolved_at earlier than created_at. The profile shows a similar rate across all rows with both values (1866 of 5319, 35.1%), so this is not just a format-parsing artefact. | Resolution times computed from these rows will be negative, which breaks SLA compliance and mean-time-to-resolve metrics. It also suggests the timestamps or the ticket linkage are wrong. | `CASE WHEN created_at ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}([T ][0-9]{2}:[0-9]{2}:[0-9]{2})?$' AND resolved_at ~ '^[0-9]{4}-[0-9]{2}-[0-9]{2}([T ][0-9]{2}:[0-9]{2}:[0-9 ... [109 more characters]` | proposed |

## Sample input and output (verbatim, one call per task)

### classify_category (claude-haiku-5-5, prompt category-v1)

Input (user message; the system prompt is in agents/prompts/):

```
<values>
{"key": "v001", "value": "access control", "rows": 176, "samples": ["Badge reader at <BLDG> main entrance not working. Staff can't get in.", "Door <N> won't lock. Handle mechanism seems broken."]}
{"key": "v002", "value": "hvac", "rows": 228, "samples": ["A/C leaking water onto the carpet near cubicle <N>. <BLDG>.", "Heating system making loud banging noises in <BLDG>, <N> floor. Started yesterday."]}
{"key": "v003", "value": "elec", "rows": 143, "samples": ["Breaker keeps tripping in server room <N>. Critical — affects production systems.", "Emergency lighting test failed in stairwell A, <BLDG>."]}
{"key": "v004", "value": "it", "rows": 148, "samples": ["Internet extremely slow in <BLDG>. Speed test showing <N> Mbps down.", "Network jack at desk <N> not active. New hire needs connectivity."]}
{"key": "v005", "value": "fire safety", "rows": 426, "samples": ["Emergency exit sign burnt out, <BLDG> <N> floor east stairwell.", "Fire alarm triggered in <BLDG>, <N> floor. No visible fire. Possible sensor malfunction."]}
{"key": "v006", "value": "fire alarm", "rows": 183, "samples": ["Emergency exit sign burnt out, <BLDG> <N> floor east stairwell.", "Fire alarm triggered in <BLDG>, <N> floor. No visible fire. Possible sensor malfunction."]}
{"key": "v007", "value": "exterminator", "rows": 276, "samples": ["Ant infestation in <BLDG>, <N> floor break room. Getting worse.", "Cockroach sighting in <BLDG> restroom. Third report this month."]}
{"key": "v008", "value": "hvac system", "rows": 109, "samples": ["A/C leaking water onto the carpet near cubicle <N>. <BLDG>.", "Heating system making loud banging noises in <BLDG>, <N> floor. Started yesterday."]}
{"key": "v009", "value": "janitorial", "rows": 333, "samples": ["Bird droppings on <BLDG> entrance canopy. Looks unprofes
... [7566 more characters]
```

Output:

```json
{
 "mappings": [
  {
   "key": "v001",
   "reason": "Access control matches badges, locks, and door access systems.",
   "category": "Security & Access",
   "confidence": 0.97
  },
  {
   "key": "v002",
   "reason": "HVAC abbreviation; samples show A/C and heating issues.",
   "category": "HVAC",
   "confidence": 0.98
  },
  {
   "key": "v003",
   "reason": "Abbreviation of electrical; samples show breakers and emergency lighting.",
   "category": "Electrical",
   "confidence": 0.92
  },
  {
   "key": "v004",
   "reason": "IT; samples concern internet speed and network jacks.",
   "category": "IT & Network",
   "confidence": 0.9
  },
  {
   "key": "v005",
   "reason": "Fire safety; samples cover fire alarms and exit signs.",
   "category": "Fire Safety",
   "confidence": 0.98
  },
  {
   "key": "v006",
   "reason": "Fire alarms are explicitly listed under Fire Safety.",
   "category": "Fire Safety",
   "confidence": 0.98
  },
  {
   "key": "v007",
   "reason": "Exterminator visits; samples show ants and cockroaches.",
   "category": "Pest Control",
   "confidence": 0.97
  },
  {
   "key": "v008",
   "reason": "HVAC system; samples show A/C and heating problems.",
   "category": "HVAC",
   "confidence": 0.97
  },
  {
   "key": "v009",
   "reason": "Janitorial work; samples show droppings and carpet stain cleaning.",
   "category": "Cleaning",
   "confidence": 0.97
  },
  {
   "key": "v010",
   "reason": "Housekeeping; samples show stains and droppings needing cleaning.",
   "category": "Cleaning",
   "confidence": 0.96
  },
  {
   "key": "v011",
   "reason": "Vertical transport; samples show elevator faults.",
   "category": "Elevator",
   "confidence": 0.98
  },
  {
   "key": "v012",
   "reason": "Climate control; samples show A/C and heating issues.",
   "category": "H
... [5219 more characters]
```

### classify_issue (claude-haiku-5-5, prompt issue-v1)

Input (user message; the system prompt is in agents/prompts/):

```
<values>
{"key": "v001", "value": "Emergency lighting test failed in stairwell South, <BLDG>.", "rows": 21, "category": "Electrical"}
{"key": "v002", "value": "Door handle broken on room <N>, <BLDG>.", "rows": 131, "category": "General Maintenance"}
{"key": "v003", "value": "water everywhere in bathroom <BLDG>", "rows": 125, "category": "Plumbing"}
{"key": "v004", "value": "Half the lights on <N> floor are out. <BLDG>.", "rows": 148, "category": "Electrical"}
{"key": "v005", "value": "Power outlet not working at desk <N>. Tried multiple devices.", "rows": 150, "category": "Electrical"}
{"key": "v006", "value": "Please check the HVAC — multiple complaints from staff on <N> floor about humidity.", "rows": 100, "category": "HVAC"}
{"key": "v007", "value": "Lights flickering in <BLDG>, <N> floor hallway. Has been happening for <N> days.", "rows": 144, "category": "Electrical"}
{"key": "v008", "value": "VPN issues from <BLDG> conference room. Can't connect to corporate.", "rows": 181, "category": "IT & Network"}
{"key": "v009", "value": "wifi password not working", "rows": 135, "category": "IT & Network"}
{"key": "v010", "value": "Faucet in <BLDG> kitchen won't turn off completely. Constant drip.", "rows": 140, "category": "Plumbing"}
{"key": "v011", "value": "Tailgating incident reported at <BLDG> loading dock. Review camera footage.", "rows": 176, "category": "Security & Access"}
{"key": "v012", "value": "Bird droppings on <BLDG> entrance canopy. Looks unprofessional.", "rows": 165, "category": "Cleaning"}
{"key": "v013", "value": "Security camera <ASSET> offline since yesterday. Covers parking lot sector <N>.", "rows": 192, "category": "Security & Access"}
{"key": "v014", "value": "Mice spotted in <BLDG> kitchen area. Droppings found in cabinets.", "rows": 190, "category"
... [4584 more characters]
```

Output:

```json
{
 "mappings": [
  {
   "key": "v001",
   "reason": "Emergency lighting test failed; life-safety device not functioning.",
   "confidence": 0.9,
   "issue_type": "emergency lighting fault",
   "hazard_signal": "safety_hazard"
  },
  {
   "key": "v002",
   "reason": "Single door handle broken; no injury risk stated.",
   "confidence": 0.9,
   "issue_type": "door handle broken",
   "hazard_signal": "routine"
  },
  {
   "key": "v003",
   "reason": "Water everywhere in shared bathroom; active leak affecting multiple users.",
   "confidence": 0.7,
   "issue_type": "bathroom water leak",
   "hazard_signal": "service_outage"
  },
  {
   "key": "v004",
   "reason": "Half of floor's lights out; shared lighting service down for many people.",
   "confidence": 0.75,
   "issue_type": "lights out",
   "hazard_signal": "service_outage"
  },
  {
   "key": "v005",
   "reason": "Single desk outlet not working; affects one workstation.",
   "confidence": 0.9,
   "issue_type": "power outlet fault",
   "hazard_signal": "routine"
  },
  {
   "key": "v006",
   "reason": "Staff complaints about humidity on a floor; no outage or hazard stated.",
   "confidence": 0.7,
   "issue_type": "humidity complaint",
   "hazard_signal": "routine"
  },
  {
   "key": "v007",
   "reason": "Hallway lights flickering; nuisance fault with no hazard stated.",
   "confidence": 0.8,
   "issue_type": "lights flickering",
   "hazard_signal": "routine"
  },
  {
   "key": "v008",
   "reason": "Conference room cannot connect to corporate VPN; shared network service affected.",
   "confidence": 0.6,
   "issue_type": "vpn connection failure",
   "hazard_signal": "service_outage"
  },
  {
   "key": "v009",
   "reason": "Individual wifi password problem; single user request.",
   "confidence": 0.85,
   "issue_type": "wifi
... [8462 more characters]
```

### classify_person (claude-haiku-5-5, prompt person-v1)

Input (user message; the system prompt is in agents/prompts/):

```
known_people: []
<values>
{"key": "v001", "value": "d. kim", "rows": 293}
{"key": "v002", "value": "chris taylor", "rows": 377}
{"key": "v003", "value": "robert martinez", "rows": 326}
{"key": "v004", "value": "r. martinez", "rows": 315}
{"key": "v005", "value": "john smith", "rows": 616}
{"key": "v006", "value": "sam jackson", "rows": 321}
{"key": "v007", "value": "j. doe", "rows": 319}
{"key": "v008", "value": "david kim", "rows": 585}
{"key": "v009", "value": "sarah williams", "rows": 321}
{"key": "v010", "value": "pat anderson", "rows": 316}
{"key": "v011", "value": "emily brown", "rows": 360}
{"key": "v012", "value": "jordan rivera", "rows": 326}
{"key": "v013", "value": "michael johnson", "rows": 313}
{"key": "v014", "value": "l. chen", "rows": 298}
{"key": "v015", "value": "mike johnson", "rows": 294}
{"key": "v016", "value": "nancy white", "rows": 287}
{"key": "v017", "value": "a. garcia", "rows": 297}
{"key": "v018", "value": "k. lee", "rows": 282}
{"key": "v019", "value": "lisa chen", "rows": 305}
{"key": "v020", "value": "e. brown", "rows": 306}
{"key": "v021", "value": "alex garcia", "rows": 297}
{"key": "v022", "value": "jane doe", "rows": 648}
{"key": "v023", "value": "j. smith", "rows": 321}
{"key": "v024", "value": "s. williams", "rows": 323}
{"key": "v025", "value": "m. johnson", "rows": 337}
{"key": "v026", "value": "tom wilson", "rows": 291}
{"key": "v027", "value": "bob martinez", "rows": 287}
{"key": "v028", "value": "t. wilson", "rows": 297}
{"key": "v029", "value": "c. taylor", "rows": 298}
{"key": "v030", "value": "karen lee", "rows": 294}
</values>
```

Output:

```json
{
 "mappings": [
  {
   "key": "v001",
   "reason": "Initial D with surname Kim matches only David Kim",
   "confidence": 0.85,
   "person_name": "David Kim"
  },
  {
   "key": "v002",
   "reason": "Exact name, title-cased",
   "confidence": 1.0,
   "person_name": "Chris Taylor"
  },
  {
   "key": "v003",
   "reason": "Exact name, title-cased",
   "confidence": 1.0,
   "person_name": "Robert Martinez"
  },
  {
   "key": "v004",
   "reason": "Initial R with surname Martinez matches only Robert Martinez",
   "confidence": 0.85,
   "person_name": "Robert Martinez"
  },
  {
   "key": "v005",
   "reason": "Exact name, title-cased",
   "confidence": 1.0,
   "person_name": "John Smith"
  },
  {
   "key": "v006",
   "reason": "Exact name, title-cased",
   "confidence": 1.0,
   "person_name": "Sam Jackson"
  },
  {
   "key": "v007",
   "reason": "Initial J with surname Doe matches only Jane Doe",
   "confidence": 0.7,
   "person_name": "Jane Doe"
  },
  {
   "key": "v008",
   "reason": "Exact name, title-cased",
   "confidence": 1.0,
   "person_name": "David Kim"
  },
  {
   "key": "v009",
   "reason": "Exact name, title-cased",
   "confidence": 1.0,
   "person_name": "Sarah Williams"
  },
  {
   "key": "v010",
   "reason": "Exact name, title-cased",
   "confidence": 1.0,
   "person_name": "Pat Anderson"
  },
  {
   "key": "v011",
   "reason": "Exact name, title-cased",
   "confidence": 1.0,
   "person_name": "Emily Brown"
  },
  {
   "key": "v012",
   "reason": "Exact name, title-cased",
   "confidence": 1.0,
   "person_name": "Jordan Rivera"
  },
  {
   "key": "v013",
   "reason": "Exact name, formal name preferred as canonical",
   "confidence": 1.0,
   "person_name": "Michael Johnson"
  },
  {
   "key": "v014",
   "reason": "Initial L with surname Chen matches only Lisa Chen
... [2302 more characters]
```

### dq_propose (claude-sonnet-5-5, prompt dq-v1)

Input (user message; the system prompt is in agents/prompts/):

```
TABLE: bronze.tickets_raw, 10280 rows. Operational support tickets for building facilities: one row per ticket.
COLUMNS (all stored as text): ticket_id (identifier), created_at (timestamp), resolved_at (timestamp), category (enumeration), priority (enumeration), status (enumeration), building (enumeration), description (free_text), submitted_by (person_name), assigned_to (enumeration), resolution_notes (free_text), cost (money), sla_hours (enumeration)

FOCUS: column ticket_id
{"column": "ticket_id", "rows": 10280, "blank": 8, "blank_pct": 0.08, "distinct": 10261, "min_length": 0, "max_length": 9, "padded_whitespace": 0, "distinct_shapes": 5, "shapes": [{"shape": "aaa-9999", "rows": 9000, "example": "TKT-1000"}, {"shape": "aaa-99999", "rows": 1254, "example": "TKT-10000"}, {"shape": "a/a", "rows": 9, "example": "N/A"}, {"shape": "aaaa", "rows": 5, "example": "NULL"}, {"shape": "99", "rows": 4, "example": "15"}], "top_values": [{"value": "N/A", "rows": 9}, {"value": "NULL", "rows": 5}, {"value": "15", "rows": 1}, {"value": "20", "rows": 1}, {"value": "90", "rows": 1}, {"value": "97", "rows": 1}, {"value": "TKT-1000", "rows": 1}, {"value": "TKT-10000", "rows": 1}, {"value": "TKT-10001", "rows": 1}, {"value": "TKT-10002", "rows": 1}, {"value": "TKT-10003", "rows": 1}, {"value": "TKT-10004", "rows": 1}], "numeric": {"rows": 4, "min": 15.0, "max": 97.0}, "tags": {"semantic_type": "identifier", "sensitivity": "none", "basis": "fixed pattern, unique"}}
```

Output:

```json
{
 "probes": [],
 "proposals": [
  {
   "sql": "ticket_id IN ('N/A','NULL')",
   "risk": "low",
   "action": "set_null",
   "columns": [
    "ticket_id"
   ],
   "finding": "14 rows hold a placeholder instead of an ID: 'N/A' in 9 rows and 'NULL' in 5 rows. Both are text strings, not real nulls.",
   "rule_id": "ticket_id_placeholder_to_null",
   "sql_kind": "row_predicate",
   "expected_rows": 14,
   "why_it_matters": "Placeholder strings pass as ordinary values in counts and joins. 'N/A' and 'NULL' would each be treated as a single ticket, which hides the missing keys and can create false matches."
  },
  {
   "sql": "ticket_id IS NULL OR ticket_id !~ '^TKT-[0-9]{4,5}$'",
   "risk": "high",
   "action": "quarantine",
   "columns": [
    "ticket_id"
   ],
   "finding": "26 of 10280 rows (0.25%) have no valid ticket ID. 8 are blank, 9 are 'N/A', 5 are 'NULL', and 4 are bare numbers (15, 20, 90, 97). All other rows match TKT-#### or TKT-#####, and every one of those IDs is unique.",
   "rule_id": "ticket_id_invalid_key_quarantine",
   "sql_kind": "row_predicate",
   "expected_rows": 26,
   "why_it_matters": "ticket_id is the key of the table, one row per ticket. Rows without a valid key cannot be joined, deduplicated or traced back to the source. Counts of tickets, SLA reporting and cost per ticket would all be affected."
  }
 ]
}
```
