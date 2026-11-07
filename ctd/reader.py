"""The model reads the interview answers and proposes contract fields; code parses the same answers and checks every field.

The contract is always built from the parsed result (ctd/builder.py). A model value only ever appears here, next to the parsed one,
and a disagreement is shown, not resolved. Values the catalog does not know are discarded.
"""
from __future__ import annotations

import json
import math
import re

from . import gateway
from .builder import KPI_SOURCE, _match_role
from .interview import WORDS, ontology_by_role, parse_cadence, parse_threshold, pick_lead_kpi

FIELDS = [("decision", "Decision"), ("cadence", "Rhythm"), ("kpi", "Number"), ("threshold", "Threshold"),
          ("direction", "Direction"), ("escalation_to", "Hand over to")]
CADENCES = {"daily", "per_shift", "weekly", "monthly"}
DIRECTIONS = {"below", "above"}
CHECK_MARKERS = [("owner active", ("Owner ",)), ("official number", ("Number ", "KPI catalog", "agreed definition")),
                 ("cadence covered by the data", ("faster than the evidence",)), ("evidence cited", ("evidence line",))]


def parsed(answers: dict) -> dict:
    """What the code takes from the answers. Same functions the contract builder uses."""
    kname = pick_lead_kpi(answers["q2"]) or pick_lead_kpi(answers["q1"])
    thr = parse_threshold(answers["q2"])
    unit = KPI_SOURCE[kname]["unit"] if kname else ""
    below = re.search(r"\b(below|under|less than)\b", answers["q2"].lower())
    return {"decision": answers["q1"].split(".")[0].strip().rstrip("?"),
            "cadence": parse_cadence(answers["q1"]),
            "kpi": kname,
            "threshold": None if thr is None else f"{thr:g} {'weeks' if unit == 'weeks' else unit}".strip(),
            "direction": "below" if below else None,
            "escalation_to": _match_role(answers["q3"])}, thr


def _words(v) -> str:
    return re.sub(r"[^a-z0-9. ]+", "", str(v).lower()).strip()


def _num(v) -> float | None:
    if isinstance(v, bool):
        return None
    try:
        if isinstance(v, (int, float)):
            f = float(v)
        else:
            s = str(v).strip().lower()[:40]
            if s in WORDS:
                return float(WORDS[s])
            m = re.match(r"^\d+(?:\.\d+)?(?!\d|\.\d|e\d|,\d)", s)
            f = float(m.group(0)) if m else None
    except (OverflowError, ValueError):
        return None
    return f if f is not None and math.isfinite(f) else None


def _allowed(key: str, v) -> str | None:
    """None when the model value is usable; otherwise the reason it is discarded."""
    if isinstance(v, bool) or not isinstance(v, (str, int, float)) or len(str(v)) > 300:
        return "not a usable value"
    if key == "kpi" and v not in KPI_SOURCE:
        return "not in the KPI catalog"
    if key == "escalation_to" and v not in ontology_by_role():
        return "not in the role list"
    if key == "cadence" and v not in CADENCES:
        return "not a known rhythm"
    if key == "direction" and v not in DIRECTIONS:
        return "not a known direction"
    if key == "threshold" and (_num(v) is None or _num(v) <= 0):
        return "not a usable number"
    return None


def _same(key: str, model, mine, thr: float | None) -> bool:
    if mine is None:
        return False
    if key == "threshold":
        return thr is not None and _num(model) == thr
    if key == "decision":
        a, b = _words(model), _words(mine)
        return len(a) >= 8 and (a == b or a in b or b in a)
    return _words(model) == _words(mine)


def _shown(key: str, v) -> str:
    if key == "threshold":
        n = _num(v)
        if n is not None:
            return f"{n:g}"
    return str(v)[:80]


def build(answers: dict, model: dict | None, source: str, why: str = "", errors: list[str] | None = None) -> dict:
    """Pair every model field with the parsed one. `model` is None when no usable proposal exists."""
    mine, thr = parsed(answers)
    texts = [a.lower() for a in answers.values()]
    rows = []
    for key, label in FIELDS:
        m = (model or {}).get(key)
        mv = m.get("value") if isinstance(m, dict) else None
        frag = m.get("fragment") if isinstance(m, dict) else None
        quote = frag if isinstance(frag, str) and len(frag.strip()) >= 3 and any(frag.strip().lower() in t for t in texts) else None
        row = {"key": key, "label": label, "parsed": mine[key], "model": None if mv is None else _shown(key, mv), "quote": quote,
               "quote_claimed": bool(frag), "status": "missing", "text": ""}
        if model is not None:
            reason = _allowed(key, mv) if mv is not None else "no value"
            if reason:
                row.update(status="rejected", text=f"model: {row['model'] or '-'} · {reason}, discarded; parsed: {mine[key] or 'nothing'}")
            elif _same(key, mv, mine[key], thr):
                row.update(status="match", text="model proposal matches parsed answer")
            else:
                row.update(status="conflict", text=f"model: {_shown(key, mv)} · parsed: {mine[key] or 'nothing'}")
        rows.append(row)
    errs = errors or []
    checks = [{"label": lab, "ok": not any(any(mk in e for mk in marks) for e in errs)} for lab, marks in CHECK_MARKERS]
    return {"rows": rows, "checks": checks, "source": source, "why": why, "has_model": model is not None,
            "conflicts": [r for r in rows if r["status"] in ("conflict", "rejected")]}


def cached(answers: dict, errors: list[str]) -> dict:
    """Fixture proposal, only when it belongs to these answers. Otherwise there is nothing honest to show on the left."""
    fx = gateway.fixture("interview_supply_analytics_lead.json")
    if {k: " ".join(v.split()).lower() for k, v in answers.items()} == {k: " ".join(v.split()).lower() for k, v in fx["answers"].items()}:
        return build(answers, fx["proposal"], "cached", "prepared answers", errors)
    return build(answers, None, "none", "no model reading for these answers", errors)


def ask_model(answers: dict, errors: list[str]) -> dict:
    """Live call through the gateway (same timeout and fallback as every other model text)."""
    facts = json.dumps({"answers": answers, "allowed": {"kpi": list(KPI_SOURCE), "escalation_to": list(ontology_by_role()),
                                                         "cadence": sorted(CADENCES), "direction": sorted(DIRECTIONS)}}, indent=1)
    task = ("Propose these contract fields from the answers: decision, cadence, kpi, threshold (a number), direction, escalation_to. "
            'Format: {"decision": {"value": "...", "fragment": "..."}, ...}. The fragment must be copied word for word from the answers.')
    r = gateway.complete_json(task, facts)
    if r["data"] is None:
        out = cached(answers, errors)
        out["why"] = r["why"]
        return out
    return build(answers, r["data"], "model", r["why"], errors)
