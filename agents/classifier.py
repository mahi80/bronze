"""Semantic Classification Agent.

Turns ambiguous values into standard, queryable ones. Three tasks, each a separate prompt and schema:
  category  raw category spellings      -> one of ten standard categories   (silver.category_map)
  person    submitter name spellings    -> one canonical person             (silver.person_map)
  issue     masked description template -> issue type and hazard signal     (silver.issue_map)

The model only ever sees distinct values that have no mapping yet, in batches. Its answers are validated,
stored with provenance, and applied by the pipeline only when approved (or auto-applied as provisional when
the policy allows). A value that is already mapped is never sent again: the mapping tables are the cache.
"""
import json
import logging
import re
from dataclasses import dataclass, field

import psycopg
import yaml

from pipeline.config import Settings

from .llm import LLM, BudgetExceeded, LLMOutputError, load_prompt

log = logging.getLogger("agents")
MAX_VALUE_CHARS = 200


@dataclass
class Item:
    value: str          # the normalised value sent to the model
    rows: int
    extra: dict = field(default_factory=dict)   # samples, category, ...


@dataclass
class Outcome:
    task: str
    results: list[dict] = field(default_factory=list)    # one dict per classified value
    failed: list[str] = field(default_factory=list)      # values whose batch failed validation
    held: list[str] = field(default_factory=list)        # values not sent (too rare, or budget spent)
    model: str = ""
    prompt_version: str = ""
    calls: int = 0
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0


def taxonomy(settings: Settings) -> dict:
    return yaml.safe_load((settings.rules_dir / "taxonomy.yaml").read_text(encoding="utf-8"))


def _clean(value: str) -> str:
    """Values are data: strip control characters and cap the length before they go into a prompt."""
    return re.sub(r"[\x00-\x1f\x7f]", " ", value)[:MAX_VALUE_CHARS]


def _batches(items: list, size: int):
    for start in range(0, len(items), size):
        yield items[start:start + size]


def _payload(batch: list[Item], header: dict | None = None) -> tuple[str, dict]:
    """Render a batch as JSON lines inside <values>. Each value gets a short key the model must echo."""
    keys = {f"v{index:03d}": item for index, item in enumerate(batch, start=1)}
    lines = [json.dumps({"key": key, "value": _clean(item.value), "rows": item.rows, **item.extra}, ensure_ascii=False)
             for key, item in keys.items()]
    head = "".join(f"{name}: {json.dumps(value, ensure_ascii=False)}\n" for name, value in (header or {}).items())
    return f"{head}<values>\n" + "\n".join(lines) + "\n</values>", keys


def _schema(properties: dict) -> dict:
    """JSON schema for {"mappings": [{key, ...properties, confidence, reason}]}; valid for strict modes."""
    item_properties = {"key": {"type": "string"}, **properties, "confidence": {"type": "number"}, "reason": {"type": "string"}}
    return {
        "type": "object", "additionalProperties": False, "required": ["mappings"],
        "properties": {"mappings": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": list(item_properties), "properties": item_properties}}},
    }


def _check_keys(mappings: list[dict], keys: dict) -> None:
    returned = [m["key"] for m in mappings]
    if sorted(returned) != sorted(keys):
        missing, extra = sorted(set(keys) - set(returned)), sorted(set(returned) - set(keys))
        raise LLMOutputError(f"keys do not match the input: missing={missing[:5]} unexpected={extra[:5]} "
                             f"duplicates={len(returned) - len(set(returned))}")
    for mapping in mappings:
        if not 0 <= mapping["confidence"] <= 1:
            raise LLMOutputError(f"confidence out of range for {mapping['key']}")


def _run(llm: LLM, task: str, system: str, version: str, schema: dict, items: list[Item], batch_size: int,
         check, header: dict | None = None, cache_salt: str = "") -> Outcome:
    outcome = Outcome(task=task, prompt_version=version)
    for batch in _batches(items, batch_size):
        user, keys = _payload(batch, header)

        def validate(data: dict, keys=keys) -> None:
            _check_keys(data["mappings"], keys)
            for mapping in data["mappings"]:
                check(mapping, keys)

        try:
            result = llm.complete_json(task=task, prompt_version=version, system=system, user=user, schema=schema,
                                       tier="small", cache_salt=cache_salt, validate=validate)
            mappings = result.data["mappings"]
        except BudgetExceeded as exc:
            log.warning("task=%s stopped: %s", task, exc)
            outcome.held.extend(item.value for item in batch)
            continue
        except LLMOutputError as exc:
            log.warning("task=%s batch rejected: %s", task, exc)
            outcome.failed.extend(item.value for item in batch)
            continue
        outcome.model = result.model
        outcome.calls += 0 if result.cached else 1
        outcome.tokens_in += result.input_tokens
        outcome.tokens_out += result.output_tokens
        outcome.latency_ms += result.latency_ms
        for mapping in mappings:
            item = keys[mapping["key"]]
            outcome.results.append({**{k: v for k, v in mapping.items() if k != "key"}, "value": item.value, "rows": item.rows})
    return outcome


# ----------------------------------------------------------------------------------------- category
def classify_category(llm: LLM, settings: Settings, items: list[Item], cache_salt: str = "") -> Outcome:
    labels = taxonomy(settings)["categories"]
    version, template = load_prompt("category")
    system = template.replace("{categories}", "\n".join(f"- {name}: {definition}" for name, definition in labels.items()))
    schema = _schema({"category": {"type": "string", "enum": [*labels, "unknown"]}})
    return _run(llm, "classify_category", system, version, schema, items, settings.batch_size, lambda m, k: None, cache_salt=cache_salt)


def category_candidates(conn: psycopg.Connection, min_rows: int) -> tuple[list[Item], list[str]]:
    """Distinct category values on valid rows that have no mapping yet, with two masked sample descriptions."""
    rows = conn.execute(
        """
        SELECT silver.norm_key(c.category_txt) AS raw_key, count(*) AS n,
               (array_agg(DISTINCT silver.mask_description(c.description_txt, c.building_txt))
                    FILTER (WHERE c.description_txt IS NOT NULL))[1:2] AS samples
        FROM silver.v_bronze_checked c
        WHERE cardinality(c.reject_reasons) = 0 AND c.category_txt IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM silver.category_map m WHERE m.raw_key = silver.norm_key(c.category_txt))
          -- rows the swap repair (R02) will fix carry a description in the category column: not a label to map
          AND NOT EXISTS (SELECT 1 FROM silver.category_map d WHERE d.raw_key = silver.norm_key(c.description_txt)
                                                                AND d.status IN ('approved', 'provisional'))
        GROUP BY 1 ORDER BY 2 DESC, 1
        """).fetchall()
    items = [Item(value, n, {"samples": [s[:120] for s in (samples or [])]}) for value, n, samples in rows if n >= min_rows]
    held = [value for value, n, _ in rows if n < min_rows]
    return items, held


# ----------------------------------------------------------------------------------------- person
def _title(name: str) -> str:
    return " ".join(part.capitalize() for part in name.split())


def person_slug(name: str) -> str:
    return re.sub(r"[^a-z]+", "-", name.lower()).strip("-")


def classify_person(llm: LLM, settings: Settings, items: list[Item], known_people: list[str], cache_salt: str = "") -> Outcome:
    version, system = load_prompt("person")
    schema = _schema({"person_name": {"type": "string"}})
    allowed = {name.lower() for name in known_people}

    def check(mapping: dict, keys: dict) -> None:
        # guardrail: the model may not invent a person. The canonical name must be known or present in the batch.
        full_names = {item.value.lower() for item in keys.values() if re.fullmatch(r"[a-z]{2,} [a-z' -]{2,}", item.value.lower())}
        name = " ".join(mapping["person_name"].split())
        if name.lower() not in allowed | full_names and name.lower() != keys[mapping["key"]].value.lower():
            raise LLMOutputError(f"person_name '{name}' is neither known nor a full spelling in the input")
        mapping["person_name"] = _title(name)

    # one batch only: resolving names needs the whole list in view
    return _run(llm, "classify_person", system, version, schema, items, max(len(items), 1), check,
                header={"known_people": sorted(known_people)}, cache_salt=cache_salt)


def person_candidates(conn: psycopg.Connection, min_rows: int) -> tuple[list[Item], list[str], list[str]]:
    rows = conn.execute(
        """
        SELECT silver.norm_key(c.submitted_by_txt) AS raw_key, count(*) AS n
        FROM silver.v_bronze_checked c
        WHERE cardinality(c.reject_reasons) = 0 AND c.submitted_by_txt IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM silver.person_map m WHERE m.raw_key = silver.norm_key(c.submitted_by_txt))
        GROUP BY 1 ORDER BY 2 DESC, 1
        """).fetchall()
    known = [r[0] for r in conn.execute("SELECT DISTINCT person_name FROM silver.person_map WHERE status IN ('approved', 'provisional')")]
    return [Item(value, n) for value, n in rows if n >= min_rows], [value for value, n in rows if n < min_rows], known


# ----------------------------------------------------------------------------------------- issue
ISSUE_TYPE = re.compile(r"[a-z][a-z0-9 /&-]{2,39}")


def classify_issue(llm: LLM, settings: Settings, items: list[Item], cache_salt: str = "") -> Outcome:
    labels = taxonomy(settings)["hazard_signals"]
    version, template = load_prompt("issue")
    system = template.replace("{hazard_signals}", "\n".join(f"  {index}. {name}: {definition}" for index, (name, definition) in enumerate(labels.items(), 1)))
    schema = _schema({"issue_type": {"type": "string"}, "hazard_signal": {"type": "string", "enum": list(labels)}})

    def check(mapping: dict, keys: dict) -> None:
        mapping["issue_type"] = " ".join(mapping["issue_type"].lower().split())
        if not ISSUE_TYPE.fullmatch(mapping["issue_type"]) or len(mapping["issue_type"].split()) > 5:
            raise LLMOutputError(f"issue_type '{mapping['issue_type'][:50]}' is not a short lower-case phrase")

    return _run(llm, "classify_issue", system, version, schema, items, settings.batch_size, check, cache_salt=cache_salt)


def issue_candidates(conn: psycopg.Connection, min_rows: int) -> tuple[list[Item], list[str]]:
    """Description templates in silver that have no mapping yet. Requires a silver build (swap repair first)."""
    rows = conn.execute(
        """
        SELECT t.description_template, count(*) AS n, min(t.category_std)
        FROM silver.tickets t
        WHERE t.description_template IS NOT NULL
          AND NOT EXISTS (SELECT 1 FROM silver.issue_map m WHERE m.raw_key = t.description_template)
        GROUP BY 1 ORDER BY 2 DESC, 1
        """).fetchall()
    return ([Item(value, n, {"category": category}) for value, n, category in rows if n >= min_rows],
            [value for value, n, _ in rows if n < min_rows])


# ----------------------------------------------------------------------------------------- storing proposals
def store(conn: psycopg.Connection, settings: Settings, outcome: Outcome) -> dict:
    """Write classified values to the mapping table as proposals. Policy decides what is applied without a person:
    category and issue mappings at or above the confidence threshold become 'provisional' (applied, listed for
    review); everything else, and every person merge, stays 'proposed' until someone approves it."""
    counts = {"provisional": 0, "proposed": 0}
    for result in outcome.results:
        meta = (result["confidence"], result["reason"][:200], outcome.model, outcome.prompt_version)
        confident = result["confidence"] >= settings.auto_apply_confidence
        if outcome.task == "classify_category":
            unknown = result["category"] == "unknown"
            status = "provisional" if confident and not unknown else "proposed"
            conn.execute(
                "INSERT INTO silver.category_map (raw_key, category_std, status, source, confidence, reason, model, prompt_version) "
                "VALUES (%s, %s, %s, 'agent', %s, %s, %s, %s) ON CONFLICT (raw_key) DO NOTHING",
                (result["value"], "Unclassified" if unknown else result["category"], status, *meta))
        elif outcome.task == "classify_person":
            status = "proposed"      # a merge is a decision about identity: always a person's call
            conn.execute(
                "INSERT INTO silver.person_map (raw_key, person_id, person_name, status, source, confidence, reason, model, prompt_version) "
                "VALUES (%s, %s, %s, %s, 'agent', %s, %s, %s, %s) ON CONFLICT (raw_key) DO NOTHING",
                (result["value"], person_slug(result["person_name"]), result["person_name"], status, *meta))
        else:
            status = "provisional" if confident else "proposed"
            conn.execute(
                "INSERT INTO silver.issue_map (raw_key, issue_type, hazard_signal, status, source, confidence, reason, model, prompt_version) "
                "VALUES (%s, %s, %s, %s, 'agent', %s, %s, %s, %s) ON CONFLICT (raw_key) DO NOTHING",
                (result["value"], result["issue_type"], result["hazard_signal"], status, *meta))
        counts[status] += 1
    conn.commit()
    return counts
