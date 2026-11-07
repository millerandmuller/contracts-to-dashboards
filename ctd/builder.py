"""Interview answers + catalog evidence -> contract proposal. Code builds it; validation decides."""
from __future__ import annotations

from .catalog import Catalog
from .interview import ontology_by_role, parse_cadence, parse_threshold, pick_lead_kpi

# Where each number comes from in the pack. Monthly series or a single snapshot.
KPI_SOURCE = {
    "Inventory Coverage": {"sheet": "Product_Market_Monthly", "col": "inventory_weeks_syn", "unit": "weeks", "series": True,
                           "plain": "weeks of stock left"},
    "On-Time In-Full": {"sheet": "Product_Market_Monthly", "col": "otif_pct_syn", "unit": "%", "series": True,
                        "plain": "deliveries on time and complete"},
    "Units Shipped": {"sheet": "Product_Market_Monthly", "col": "units_shipped_syn", "unit": "units", "series": True,
                      "plain": "units sent out"},
    "Demand Forecast Accuracy": {"sheet": "Supply_Operations", "col": "forecast_accuracy_pct_syn", "unit": "%",
                                 "series": False, "plain": "how close the forecast was"},
}


def scope_from_cluster(cat: Catalog, analysis: dict, product_name: str) -> dict:
    """Which product-market pairs the contract watches: those named by the repeated requests."""
    pid = cat.products[cat.products.product_name == product_name].index[0]
    markets: list[str] = []
    for r in analysis["requests"]:
        if r["product_context"] == product_name:
            for m in cat.market_ids_for(r["market_context"]):
                if m not in markets and not cat.series(pid, m)[-1]["value"] is None:
                    markets.append(m)
    return {"product_id": pid, "product_name": product_name, "markets": markets,
            "request_ids": [r["request_id"] for r in analysis["requests"] if r["product_context"] == product_name]}


def _match_role(text: str) -> str:
    """Match the typed role to the role list, ignoring case and spaces."""
    t = " ".join(text.split()).lower()
    for r in ontology_by_role():
        if r.lower() == t:
            return r
    return " ".join(text.split())


def propose(cat: Catalog, role: str, answers: dict, analysis: dict, product_name: str = "Xarelto") -> dict:
    o = ontology_by_role()[role]
    thr = parse_threshold(answers["q2"])
    kname = pick_lead_kpi(answers["q2"]) or pick_lead_kpi(answers["q1"])
    if kname is None or thr is None:
        raise ValueError("Which number tells you to act, and at what level? Please say it like 'stock below four weeks'.")
    lead = cat.canonical_kpi(kname)
    needed = [k for k in analysis["needed"] if k != lead]
    owner = next((i for i, r in cat.owners.iterrows() if r.role == role and r.status == "active"), None)
    escal = _match_role(answers["q3"])
    scope = scope_from_cluster(cat, analysis, product_name)
    cadence = parse_cadence(answers["q1"])
    cov_assets = [a["asset_id"] for a in analysis["assets"]]
    return {
        "contract_id": "CON-SUPPLY-RISK",
        "role": role,
        "owner_id": owner,
        "decision": answers["q1"].split(".")[0].strip().rstrip("?") + ".",
        "cadence": cadence,
        "lead_kpi": {"kpi_id": lead, "threshold": {"amber": thr, "red": round(thr / 2, 2),
                                                   "unit": KPI_SOURCE[kname]["unit"], "direction": "below"}},
        "neighbour_kpis": needed[:2],
        "scope": scope,
        "upstream_roles": o["upstream"],
        "downstream_roles": [escal],
        "permitted_action": o["example_action"] if answers["q2"].lower().find("flag") < 0 else "flag the medicine and country for review",
        "escalation_to": escal,
        "escalation_when": "lead number below the amber line for 2 months in a row",
        "evidence": [
            {"sheet": "Business_Requests", "ids": scope["request_ids"], "claim": f"{product_name} asked for {len(scope['request_ids'])} times; "
                                                                                f"{len(analysis['requests'])} times in total"},
            {"sheet": "Analytics_Assets", "ids": cov_assets, "claim": f"{len(cov_assets)} assets already carry {len(analysis['covered_kpis'])} of {len(analysis['needed'])} numbers"},
            {"sheet": "KPI_Catalog", "ids": [lead] + needed, "claim": "canonical definitions used"},
            {"sheet": "Product_Market_Monthly", "ids": [f"{scope['product_id']}/{m}" for m in scope["markets"]], "claim": "monthly data, ten months"},
        ],
        "views": [
            {"for_role": role, "focus_rule": "lead number first, escalated pairs first, then worst"},
            {"for_role": escal, "focus_rule": "escalated pairs only"},
        ],
        "replaces": [],
        "preservation_fixtures": [],
    }
