"""Interview: three fixed questions. Wording and examples come from the ontology file, never from the model."""
from __future__ import annotations

import math
import re
from functools import lru_cache
from pathlib import Path

import yaml

DATA = Path(__file__).resolve().parent.parent / "data"

WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8, "nine": 9, "ten": 10}

# Everyday words -> canonical KPI name. Small, readable, extendable. The model never picks the number.
SYNONYMS = {"stock": "Inventory Coverage", "inventory": "Inventory Coverage", "supply": "Inventory Coverage",
            "delivery": "On-Time In-Full", "deliveries": "On-Time In-Full", "late": "On-Time In-Full"}


@lru_cache(maxsize=1)
def ontology() -> list[dict]:
    return yaml.safe_load((DATA / "role_ontology.yaml").read_text())


def ontology_by_role() -> dict[str, dict]:
    return {r["role"]: r for r in ontology()}


def questions(role: str) -> list[dict]:
    o = ontology_by_role()[role]
    return [
        {"id": "q1", "text": f"What do you decide with this? For example: {o['example_decision']}. How often?"},
        {"id": "q2", "text": f"When do you have to act? For example: {o['example_signal']}. And what do you do then?"},
        {"id": "q3", "text": f"Who do you call when you are stuck? For example: {', '.join(o['example_escalation'])}."},
    ]


def _tok(s: str) -> set[str]:
    return set(re.findall(r"[a-z]+", s.lower()))


def ownership_check(role: str, decision_text: str) -> str | None:
    """Ask 'Is that your call?' when the decision reads like another role's decision."""
    own = _tok(ontology_by_role()[role]["example_decision"] + " " + " ".join(ontology_by_role()[role]["vocabulary"]))
    t = _tok(decision_text) - {"the", "a", "of", "to", "is", "in", "or", "and", "which", "whether", "this", "that", "month", "once", "every"}
    if t & own:
        return None
    for r in ontology():
        if r["role"] == role:
            continue
        other = _tok(r["example_decision"] + " " + " ".join(r["vocabulary"]))
        if len(t & other) >= 2:
            return f"Is that your call? That sounds like a decision for the {r['role']}."
    return None


def parse_threshold(text: str) -> float | None:
    m = re.search(r"(?:below|under|less than)\s+(\d+(?:\.\d+)?(?![\d.e])|[a-z]+)", text.lower())
    if not m:
        return None
    v = m.group(1)
    if v in WORDS:
        return float(WORDS[v])
    try:
        f = float(v)
    except ValueError:
        return None
    return f if math.isfinite(f) and f > 0 else None


def parse_cadence(text: str) -> str:
    t = text.lower()
    for word, c in [("daily", "daily"), ("every day", "daily"), ("every shift", "per_shift"), ("weekly", "weekly"),
                    ("every week", "weekly"), ("monthly", "monthly"), ("once a month", "monthly"), ("every month", "monthly")]:
        if word in t:
            return c
    return "monthly"


def pick_lead_kpi(text: str) -> str | None:
    for w in re.findall(r"[a-z]+", text.lower()):
        if w in SYNONYMS:
            return SYNONYMS[w]
    return None
