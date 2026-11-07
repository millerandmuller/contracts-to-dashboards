"""Preservation test: a consolidation must not silently flip a decision. Runs fixtures, blocks, a human decides.

The formulas are synthetic ([GEMOCKT/KURATIERT], data/formulas.yaml). The mechanics are real.
"""
from __future__ import annotations

from functools import lru_cache
from pathlib import Path

import yaml

from . import contracts, store
from .catalog import Catalog
from .evaluate import status_of

DATA = Path(__file__).resolve().parent.parent / "data"
WEEKS_PER_MONTH = 4.33
RANK = {"red": 0, "amber": 1, "ok": 2}


@lru_cache(maxsize=1)
def config() -> dict:
    return yaml.safe_load((DATA / "formulas.yaml").read_text())


def coverage_weeks(formula: str, units: list[int], weeks_now: float) -> float | None:
    """Inventory coverage under a named formula, from the pack's monthly series (latest month)."""
    if units[-1] == 0:
        return None
    stock = weeks_now * units[-1] / WEEKS_PER_MONTH
    if formula == "F-INV-COV-v1":
        denom = units[-1]
    elif formula == "F-INV-COV-T3":
        denom = sum(units[-3:]) / len(units[-3:])
    else:
        raise KeyError(formula)
    return stock / (denom / WEEKS_PER_MONTH)


def get_proposal(pid: str) -> dict:
    return next(p for p in config()["proposals"] if p["id"] == pid)


def run(cat: Catalog, c: dict, proposal_id: str, keep_formula: str | None = None) -> dict:
    p = dict(get_proposal(proposal_id))
    if keep_formula:
        p["keep_formula"] = keep_formula
    thr = c["lead_kpi"]["threshold"]
    handover_pairs = {h["pair"] for h in __import__("ctd.handovers", fromlist=["x"]).all_for(c["contract_id"]) if h["state"] != "closed"}
    rows = []
    for m in c["scope"]["markets"]:
        s = cat.series(c["scope"]["product_id"], m, "inventory_weeks_syn")
        units = [x["units"] for x in s]
        now = s[-1]["value"]
        if now is None:
            continue
        before = coverage_weeks(p["retire_formula"], units, now)
        after = coverage_weeks(p["keep_formula"], units, now)
        sb, sa = status_of(before, thr), status_of(after, thr)
        pair = f"{c['scope']['product_id']}/{m}"
        rows.append({"pair": pair, "market": cat.markets.loc[m, "market_name"], "before": round(before, 2), "after": round(after, 2),
                     "status_before": sb, "status_after": sa, "flipped": sb != sa,
                     "hides_risk": RANK[sa] > RANK[sb],
                     "hides_open_handover": RANK[sa] > RANK[sb] and pair in handover_pairs})
    rows.sort(key=lambda r: (not r["hides_open_handover"], not r["hides_risk"], not r["flipped"]))
    flipped = [r for r in rows if r["flipped"]]
    return {
        "proposal": p, "rows": rows, "blocked": bool(flipped), "affected_role": c["escalation_to"],
        "retire_asset": cat.assets.loc[p["retire"], "asset_name"], "keep_asset": cat.assets.loc[p["keep"], "asset_name"],
        "formula_labels": {k: v["label"] for k, v in config()["formulas"].items()},
        "decisions": store.decisions(f"consolidation:{proposal_id}"),
    }


def decide(cat: Catalog, c: dict, proposal_id: str, choice: str, by: str, reason: str) -> dict:
    if not reason.strip():
        raise ValueError("A decision needs a reason.")
    res = run(cat, c, proposal_id)
    p = res["proposal"]
    if choice == "keep_exception":
        store.put_decision(f"consolidation:{proposal_id}", {"choice": choice, "by": by, "reason": reason})
        store.audit(by, "consolidation_blocked_exception_kept", proposal_id,
                    f"kept {p['retire']} as documented exception: {reason}",
                    "; ".join(f"{r['pair']} {r['status_before']}->{r['status_after']}" for r in res["rows"] if r["flipped"]))
        return {"outcome": "exception", "result": res}
    if choice == "fix_mapping":
        again = run(cat, c, proposal_id, keep_formula=p["retire_formula"])
        if again["blocked"]:
            raise ValueError("Still flips after the fix.")
        store.put_decision(f"consolidation:{proposal_id}", {"choice": choice, "by": by, "reason": reason})
        c2 = dict(c)
        c2["replaces"] = sorted(set(c["replaces"]) | {p["retire"]})
        saved = contracts.save(c2, by, f"retired {p['retire']} after mapping fix: {reason}")
        store.audit(by, "consolidation_approved", proposal_id, f"mapping corrected to {p['retire_formula']}, {p['retire']} retired")
        return {"outcome": "fixed", "result": again, "contract": saved}
    if choice == "approve":
        if res["blocked"]:
            raise ValueError("The test blocks this. Choose keep exception or fix mapping.")
        store.put_decision(f"consolidation:{proposal_id}", {"choice": choice, "by": by, "reason": reason})
        c2 = dict(c)
        c2["replaces"] = sorted(set(c["replaces"]) | {p["retire"]})
        saved = contracts.save(c2, by, f"retired {p['retire']}, preservation test passed: {reason}")
        return {"outcome": "approved", "result": res, "contract": saved}
    raise ValueError("Unknown choice.")
