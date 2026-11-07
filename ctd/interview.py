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
    qs = [("q1", "What do you decide with this? How often?", o["example_decision"], "the business question of the page, and how often it is asked"),
          ("q2", "When do you have to act? And what do you do then?", o["example_signal"], "the lead number, its line (PL), and what you may do when ΔPL turns unfavourable"),
          ("q3", "Who do you call when you are stuck?", ", ".join(o["example_escalation"]), "who receives your handovers, and who sits next to you")]
    full = {"q1": "What do you decide with this? For example: {}. How often?", "q2": "When do you have to act? For example: {}. And what do you do then?",
            "q3": "Who do you call when you are stuck? For example: {}."}
    return [{"id": i, "text": full[i].format(ex), "ask": ask, "example": ex, "goes_to": goes} for i, ask, ex, goes in qs]


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
    m = re.search(r"(?:below|under|less than)\s+(\d+(?:\.\d+)?(?!\d|\.\d|e\d)|[a-z]+)", text.lower())
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
