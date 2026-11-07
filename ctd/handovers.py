"""Handovers: a record with a state (open / acknowledged / closed). The chain lives once; both views show it."""
from __future__ import annotations

import json
from datetime import datetime

from . import store
from .catalog import Catalog
from .evaluate import pair_rows
from .interview import ontology_by_role

STATES = ("open", "acknowledged", "closed")
RESPONSE_HOURS = 48  # [KURATIERT] default window


class NotAllowed(Exception):
    pass


def _next_id() -> str:
    with store.conn() as db:
        n = db.execute("SELECT COUNT(*) FROM handovers").fetchone()[0] + 1
    return f"HO-{datetime.now():%Y-%m}-{n:04d}"


def open_from_rule(cat: Catalog, c: dict) -> list[dict]:
    """System opens one handover per escalated pair that has no live handover yet. Worst pair first."""
    made = []
    for r in pair_rows(cat, c):
        if not r["escalated"]:
            continue
        if _live(c["contract_id"], r["pair"]):
            continue
        hid = _next_id()
        thr = c["lead_kpi"]["threshold"]
        body = {
            "handover_id": hid, "contract_id": c["contract_id"], "contract_version": c["version"],
            "from_role": c["role"], "to_role": c["escalation_to"], "pair": r["pair"],
            "trigger": {"kpi_id": c["lead_kpi"]["kpi_id"], "observed": r["latest"], "threshold": thr["amber"], "unit": thr["unit"],
                        "pair": r["pair"], "months_below": 2},
            "evidence_snapshot": [{"sheet": "Product_Market_Monthly", "ids": [f"{r['pair']}/{p['month']}" for p in r["series"][-2:]],
                                   "claim": f"stock {r['series'][-2]['value']:.1f} then {r['series'][-1]['value']:.1f} weeks, both below {thr['amber']:g}"}],
            "requested_action": ontology_by_role()[c["escalation_to"]]["example_action"],
            "response_window_hours": RESPONSE_HOURS,
            "system_of_record": "ERP / supply planning system (this record points to it, it does not replace it)",
            "observed_blocker": f"{r['market_name']}: stock below {thr['amber']:g} weeks two months in a row",
            "suspected_cause": "",  # text of the sender role, never the model
        }
        with store.conn() as db:
            db.execute("INSERT INTO handovers VALUES (?,?,?,?,?,?)", (hid, c["contract_id"], r["pair"], json.dumps(body), "open", store.now()))
            db.execute("INSERT INTO transitions (handover_id,to_state,by,at,reason) VALUES (?,?,?,?,?)",
                       (hid, "open", f"system on behalf of {c['role']}", store.now(), "threshold crossed two months in a row"))
        store.audit(f"system on behalf of {c['role']}", "handover_opened", hid, f"{r['pair']} below {thr['amber']:g} {thr['unit']} two months running")
        made.append(get(hid))
    return made


def _live(contract_id: str, pair: str) -> bool:
    with store.conn() as db:
        return db.execute("SELECT 1 FROM handovers WHERE contract_id=? AND pair=? AND state!='closed'", (contract_id, pair)).fetchone() is not None


def get(hid: str) -> dict:
    with store.conn() as db:
        h = db.execute("SELECT * FROM handovers WHERE handover_id=?", (hid,)).fetchone()
        tr = [dict(r) for r in db.execute("SELECT * FROM transitions WHERE handover_id=? ORDER BY id", (hid,))]
    d = json.loads(h["body"])
    d["state"] = h["state"]
    d["created_at"] = h["created_at"]
    d["transitions"] = tr
    d["overdue"] = overdue(d)
    return d


def all_for(contract_id: str) -> list[dict]:
    with store.conn() as db:
        ids = [r[0] for r in db.execute("SELECT handover_id FROM handovers WHERE contract_id=? ORDER BY created_at", (contract_id,))]
    return [get(i) for i in ids]


def overdue(h: dict) -> bool:
    """Computed, never a state, never green. Only open handovers can be overdue."""
    if h["state"] != "open":
        return False
    age = (datetime.now() - datetime.fromisoformat(h["created_at"])).total_seconds() / 3600
    return age > h["response_window_hours"]


def acknowledge(hid: str, actor_role: str, note: str = "seen, taking it over") -> dict:
    h = get(hid)
    if actor_role != h["to_role"]:
        raise NotAllowed(f"Only the {h['to_role']} can acknowledge this. You are the {actor_role}.")
    if h["state"] != "open":
        raise NotAllowed(f"This handover is already {h['state']}.")
    _transition(hid, "acknowledged", actor_role, note)
    return get(hid)


def close(hid: str, actor_role: str, reason: str) -> dict:
    h = get(hid)
    if actor_role not in (h["from_role"], h["to_role"]):
        raise NotAllowed("Only the two roles in this handover can close it.")
    if not reason.strip():
        raise NotAllowed("Closing needs a reason.")
    if h["state"] == "closed":
        raise NotAllowed("Already closed.")
    _transition(hid, "closed", actor_role, reason)
    return get(hid)


def _transition(hid: str, to: str, by: str, reason: str) -> None:
    with store.conn() as db:
        db.execute("UPDATE handovers SET state=? WHERE handover_id=?", (to, hid))
        db.execute("INSERT INTO transitions (handover_id,to_state,by,at,reason) VALUES (?,?,?,?,?)", (hid, to, by, store.now(), reason))
    store.audit(by, f"handover_{to}", hid, reason)


def counters(contract_id: str, role: str) -> dict:
    """'Waiting on me' / 'I am waiting on others', oldest first. Fixed place in every view."""
    hs = [h for h in all_for(contract_id) if h["state"] != "closed"]
    on_me = sorted([h for h in hs if h["to_role"] == role and h["state"] == "open"], key=lambda h: h["created_at"])
    on_others = sorted([h for h in hs if h["from_role"] == role], key=lambda h: h["created_at"])
    return {"on_me": on_me, "on_others": on_others}
