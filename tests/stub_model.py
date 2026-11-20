"""A deterministic stand-in for a model, used only by the tests. It is not a model and proves nothing about
model quality; it exists to exercise the plumbing: batching, schema validation, retries, guardrails, approval."""
import csv
import json
import re
from pathlib import Path

GOLDEN = Path(__file__).resolve().parent.parent / "evals" / "golden"


def _golden(name: str, key: str, value: str) -> dict:
    with (GOLDEN / name).open(newline="", encoding="utf-8") as handle:
        return {row[key]: row[value] for row in csv.DictReader(handle)}


def _values(user: str) -> list[dict]:
    block = user.split("<values>\n", 1)[1].split("\n</values>", 1)[0]
    return [json.loads(line) for line in block.splitlines()]


class StubModel:
    """Answers from the golden sets, with optional planted mistakes. Records every call it receives."""

    def __init__(self, wrong: dict | None = None, low_confidence: set | None = None, drop_key_once: bool = False,
                 invent_person: bool = False, dq_script: dict | None = None):
        self.wrong = wrong or {}
        self.low_confidence = low_confidence or set()
        self.drop_key_once = drop_key_once
        self.invent_person = invent_person
        self.dq_script = dq_script or {}
        self.calls = []

    def __call__(self, model, system, user, schema, max_tokens):
        self.calls.append({"model": model, "system": system, "user": user})
        properties = schema["properties"]
        if "proposals" in properties:
            data = self._dq(user)
        else:
            item = properties["mappings"]["items"]["properties"]
            task = "category" if "category" in item else "person" if "person_name" in item else "issue"
            data = {"mappings": [self._answer(task, v) for v in _values(user)]}
            if self.drop_key_once:
                self.drop_key_once = False
                data["mappings"] = data["mappings"][1:]
        return json.dumps(data), len(system + user) // 4, len(json.dumps(data)) // 4

    def _answer(self, task: str, v: dict) -> dict:
        value = v["value"]
        confidence = 0.6 if value in self.low_confidence else 0.97
        base = {"key": v["key"], "confidence": confidence, "reason": "stub"}
        if task == "category":
            label = self.wrong.get(value) or _golden("category.csv", "raw_key", "category_std").get(value, "unknown")
            return {**base, "category": label}
        if task == "person":
            name = "Zaphod Beeblebrox" if self.invent_person else _golden("person.csv", "raw_key", "person_name").get(value, value.title())
            return {**base, "person_name": name}
        golden = {}
        with (GOLDEN / "issue.csv").open(newline="", encoding="utf-8") as handle:
            for row in csv.DictReader(handle):
                golden[row["template"]] = row
        match = golden.get(value) or next((row for t, row in golden.items() if t.startswith(value)), None)
        return {**base, "issue_type": match["issue_type"] if match else "unlabelled issue",
                "hazard_signal": self.wrong.get(value) or (match["hazard_signal"] if match else "routine")}

    def _dq(self, user: str) -> dict:
        focus = re.search(r"FOCUS: (?:column )?(\S+)", user).group(1)
        script = self.dq_script.get(focus, {})
        if script.get("probes") and "PROBE RESULTS" not in user:
            return {"probes": script["probes"], "proposals": []}
        return {"probes": [], "proposals": script.get("proposals", [])}
