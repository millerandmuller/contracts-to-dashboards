"""Evaluate a contract against the catalog: status per pair, escalation rule. Deterministic, no model."""
from __future__ import annotations

from .builder import KPI_SOURCE
from .catalog import Catalog

RULE_VERSION = 1  # the escalation rule: lead number below the amber line for 2 months in a row
STATUS_ORDER = {"red": 0, "amber": 1, "ok": 2, "no data": 3}


def status_of(value: float | None, thr: dict) -> str:
    if value is None:
        return "no data"
    if value < thr["red"]:
        return "red"
    if value < thr["amber"]:
        return "amber"
    return "ok"


def kpi_name(cat: Catalog, kpi_id: str) -> str:
    return str(cat.kpis.loc[kpi_id, "canonical_metric"])


def pair_rows(cat: Catalog, c: dict) -> list[dict]:
    """One row per watched product-market pair: lead series, status, escalation (2 months below amber)."""
    thr = c["lead_kpi"]["threshold"]
    lname = kpi_name(cat, c["lead_kpi"]["kpi_id"])
    col = KPI_SOURCE[lname]["col"]
    pid = c["scope"]["product_id"]
    rows = []
    for m in c["scope"]["markets"]:
        s = cat.series(pid, m, col)
        vals = [p["value"] for p in s]
        last, prev = vals[-1], vals[-2] if len(vals) > 1 else None
        below2 = last is not None and prev is not None and last < thr["amber"] and prev < thr["amber"]
        rows.append({"pair": f"{pid}/{m}", "market": m, "market_name": cat.markets.loc[m, "market_name"],
                     "series": s, "latest": last, "status": status_of(last, thr), "escalated": below2,
                     "last_kind": s[-1]["kind"], "month": s[-1]["month"]})
    return sorted(rows, key=lambda r: (not r["escalated"], STATUS_ORDER[r["status"]], r["latest"] if r["latest"] is not None else 99))


def neighbour_values(cat: Catalog, c: dict, pair: str) -> list[dict]:
    pid, m = pair.split("/")
    out = []
    for k in c["neighbour_kpis"]:
        name = kpi_name(cat, k)
        src = KPI_SOURCE[name]
        if src["series"]:
            s = cat.series(pid, m, src["col"])
            out.append({"kpi_id": k, "name": name, "plain": src["plain"], "unit": src["unit"], "series": s,
                        "latest": s[-1]["value"], "cite": f"{src['sheet']}: {pair}/{s[-1]['month']}"})
        else:
            so = cat.s["Supply_Operations"]
            r = so[(so.product_id == pid) & (so.market_id == m)]
            v = float(r.iloc[0][src["col"]]) if len(r) else None
            out.append({"kpi_id": k, "name": name, "plain": src["plain"], "unit": src["unit"], "series": [],
                        "latest": v, "cite": f"{src['sheet']}: {pair}", "gap": "Only one value in the catalog. No monthly line."})
    return out
