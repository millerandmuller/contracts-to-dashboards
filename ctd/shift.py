"""Shift change as a handover, on CURATED shift data (data/curated/shifts.csv). Not from the pack, not from any plant.

The pack has no shift data, so the refusal stays first. This module shows what the same contract form does once shift
data exists: the outgoing shift lead hands open points to the incoming one, who acknowledges at shift start.
The curated contract is kept apart from the pack's catalog: it never counts as an asset there.
"""
from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path

import pandas as pd

from . import handovers, store
from .interview import ontology_by_role, questions

DATA = Path(__file__).resolve().parent.parent / "data"
CSV = DATA / "curated" / "shifts.csv"
CID = "CON-SHIFT-CHANGE"
ROLE = "Shift Team Lead"
OUT, IN = "Shift Team Lead, late shift", "Shift Team Lead, night shift"
BADGE = "curated shift data, not from the pack, not from any plant"
ANSWERS = {  # [curated] the shift lead's three answers, written for the demo
    "q1": "Whether my lines run as planned this shift. Every shift.",
    "q2": "More than 45 minutes stopped in one shift. Then I re-plan the shift and call maintenance.",
    "q3": "Maintenance for the machine, the QA Specialist for batches waiting for release.",
}
LINE = 45  # minutes of downtime per shift, from answer 2
SYSTEM_OF_RECORD = "MES shift log and QMS (this record points to them, it does not replace them)"


@lru_cache(maxsize=1)
def data() -> pd.DataFrame:
    order = {"early": 0, "late": 1, "night": 2}
    d = pd.read_csv(CSV, keep_default_na=False)
    return d.assign(o=d["shift"].map(order)).sort_values(["date", "o", "line"]).drop(columns="o").reset_index(drop=True)


def contract() -> dict:
    o = ontology_by_role()[ROLE]
    return {
        "contract_id": CID, "curated": True, "role": ROLE, "owner_id": "none in the pack (curated)",
        "decision": "Do my lines run as planned this shift?", "cadence": "every shift",
        "lead_kpi": {"name": "Downtime", "plain": "minutes a line stood still in one shift", "line": LINE, "unit": "min", "direction": "above is unfavourable"},
        "neighbours": ["Output against plan", "Material missing at the line", "Batches waiting for QC release"],
        "upstream_roles": o["upstream"], "downstream_roles": o["downstream"],
        "permitted_action": "re-plan the shift, call maintenance", "escalation_to": o["example_escalation"],
        "handover_at": "shift change: the outgoing lead hands open points to the incoming lead",
        "views": [{"for_role": OUT, "focus_rule": "my last shift, the line with the most downtime first"},
                  {"for_role": IN, "focus_rule": "what I take over at shift start"}],
        "evidence": [{"sheet": "data/curated/shifts.csv", "ids": [str(data().shift_id.iloc[0]), str(data().shift_id.iloc[-1])], "claim": f"{len(data())} shifts, curated"}],
        "answers": ANSWERS, "version": 1,
    }


def latest() -> dict | None:
    with store.conn() as db:
        r = db.execute("SELECT body FROM contracts WHERE contract_id=? ORDER BY version DESC LIMIT 1", (CID,)).fetchone()
    return json.loads(r["body"]) if r else None


def last_shift() -> pd.DataFrame:
    d = data()
    last = d.iloc[-1]
    return d[(d.date == last.date) & (d["shift"] == last["shift"])]


def open_points(rows: pd.DataFrame) -> list[dict]:
    pts = []
    for _, r in rows.iterrows():
        if r.downtime_min > LINE:
            pts.append({"what": f"{r.line} stood still {r.downtime_min} min ({r.downtime_reason}), {r.downtime_min - LINE} min above the line",
                        "to": "Maintenance / Plant Mechanic", "cite": r.shift_id})
        if r.material_short:
            pts.append({"what": f"{r.line}: {r.material_short} missing at the line", "to": "Production Planning", "cite": r.shift_id})
        if r.batches_pending_release:
            pts.append({"what": f"{r.line}: {r.batches_pending_release} batch{'es' if r.batches_pending_release > 1 else ''} waiting for QC release",
                        "to": "QA Specialist", "cite": r.shift_id})
    return pts


def create() -> dict:
    """Idempotent: one curated contract, one shift-change handover. A second click changes nothing."""
    with store.WRITE_LOCK:
        c = latest()
        if c:
            return c
        c = contract()
        with store.conn() as db:
            db.execute("INSERT INTO contracts VALUES (?,?,?,?)", (CID, 1, json.dumps(c), store.now()))
        store.audit(ROLE, "contract_saved", f"{CID} v1 (curated)", "created from the shift lead's three answers on curated shift data")
        rows = last_shift()
        last = rows.iloc[0]
        hid = handovers._next_id()
        body = {"handover_id": hid, "contract_id": CID, "contract_version": 1, "kind": "shift_change", "from_role": OUT, "to_role": IN,
                "pair": f"{last.date} {last['shift']} shift", "points": open_points(rows), "trigger": {"at": "shift change"},
                "evidence_snapshot": [{"sheet": "data/curated/shifts.csv", "ids": list(rows.shift_id), "claim": BADGE}],
                "requested_action": "take over the open points at shift start", "response_window_hours": 1,
                "system_of_record": SYSTEM_OF_RECORD, "observed_blocker": "", "suspected_cause": ""}
        with store.conn() as db:
            db.execute("INSERT INTO handovers VALUES (?,?,?,?,?,?)", (hid, CID, body["pair"], json.dumps(body), "open", store.now()))
            db.execute("INSERT INTO transitions (handover_id,to_state,by,at,reason) VALUES (?,?,?,?,?)", (hid, "open", OUT, store.now(), "shift change"))
        store.audit(OUT, "handover_opened", hid, f"shift change, {len(body['points'])} open points")
        return c


def series(line: str, n: int = 9) -> list[dict]:
    d = data()
    d = d[d.line == line].tail(n)
    return [{"label": f"{r.date[5:]} {r['shift'][0].upper()}", "letter": r["shift"][0].upper(), "value": float(r.downtime_min), "kind": "actual",
             "id": r.shift_id} for _, r in d.iterrows()]


def view_model(c: dict, role: str) -> dict:
    rows = last_shift()
    worst = rows.sort_values("downtime_min", ascending=False).iloc[0]
    prev = data()[(data().line == worst.line)].iloc[-2]
    hs = handovers.all_for(CID)
    out = role == OUT
    ac = float(worst.downtime_min)
    nb = []
    for _, r in rows.sort_values("line").iterrows():
        nb.append({"line": r.line, "out_pct": r.produced_units / r.planned_units * 100, "produced": int(r.produced_units), "planned": int(r.planned_units),
                   "material": r.material_short or "nothing missing", "pending": int(r.batches_pending_release), "id": r.shift_id})
    return {
        "role": role, "is_receiver": not out, "c": c, "badge": BADGE,
        "kpi": {"ac": ac, "pl": float(LINE), "d": ac - LINE, "pct": (ac - LINE) / LINE * 100, "fav": ac <= LINE, "line": worst.line, "id": worst.shift_id,
                "prev": float(prev.downtime_min), "prev_id": prev.shift_id, "reason": worst.downtime_reason, "when": f"{worst.date} {worst['shift']} shift"},
        "series": series(worst.line), "lines": [{"line": r.line, "down": int(r.downtime_min), "reason": r.downtime_reason or "none", "id": r.shift_id,
                                                 "d": int(r.downtime_min) - LINE} for _, r in rows.sort_values("downtime_min", ascending=False).iterrows()],
        "neighbours": nb, "handovers": hs,
        "counters": {"on_me": [h for h in hs if h["to_role"] == role and h["state"] == "open"],
                     "on_others": [h for h in hs if h["from_role"] == role and h["state"] != "closed"]},
        "question": "What do I hand over before I leave?" if out else "What do I take over at shift start?",
        "before": [] if out else [OUT], "after": c["downstream_roles"] if not out else [IN],
        "questions": questions(ROLE),
    }
