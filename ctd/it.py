"""IT view: the same contract seen from the semantic models behind it. Refresh, endorsement, lineage. Deterministic, no model.

Which models: those feeding the supply and inventory assets of the contract's product, read from Lineage_Edges (MODEL_TO_ASSET).
"""
from __future__ import annotations

import json

from . import contracts, handovers, store
from .catalog import Catalog
from .interview import ontology_by_role

ROLE = "Semantic Model Owner"
VIEW = {"for_role": ROLE, "focus_rule": "models behind the contract, failed refresh first", "kind": "it"}
REFRESH_ORDER = {"failed": 0, "warning": 1, "healthy": 2}
SHAPE = {"failed": "○", "warning": "◐", "healthy": "●"}
SYSTEM_OF_RECORD = "BI platform refresh history (this record points to it, it does not replace it)"


def scope_assets(cat: Catalog, c: dict):
    """Supply and inventory assets of the contract's product: the ones the contract reuses or retires."""
    a = cat.assets
    return a[(a.product_scope == c["scope"]["product_name"]) & a.asset_name.str.contains("Supply|Inventory", case=False)]


def models_behind(cat: Catalog, c: dict) -> list[dict]:
    le = cat.s["Lineage_Edges"]
    m2a = le[le.edge_type == "MODEL_TO_ASSET"]
    sm = cat.s["Semantic_Models"].set_index("model_id")
    rows: dict[str, dict] = {}
    for aid, r in scope_assets(cat, c).iterrows():
        for mid in m2a[m2a.target_id == aid].source_id:
            m = sm.loc[mid]
            row = rows.setdefault(mid, {
                "model_id": mid, "name": str(m.model_name), "endorsement": str(m.endorsement), "frequency": str(m.refresh_frequency),
                "refresh": str(m.refresh_status), "shape": SHAPE[str(m.refresh_status)], "owner_id": str(m.owner_id),
                "owner_state": cat.owner_state(m.owner_id), "owner_role": cat.owner_role(m.owner_id),
                "ai": str(m.ai_readiness), "fed": sorted(m2a[m2a.source_id == mid].target_id), "scope_assets": []})
            row["scope_assets"].append({"asset_id": aid, "name": str(r.asset_name), "endorsement": str(r.endorsement)})
    return sorted(rows.values(), key=lambda r: (REFRESH_ORDER[r["refresh"]], r["model_id"]))


def catalog_wide(cat: Catalog) -> dict:
    sm = cat.s["Semantic_Models"]
    failed = sm[sm.refresh_status == "failed"]
    return {"models": len(sm), "failed": len(failed), "failed_certified": int((failed.endorsement == "certified").sum()),
            "failed_ids": list(failed.model_id), "warning": int((sm.refresh_status == "warning").sum()),
            "poor_ai": int((sm.ai_readiness == "poor").sum())}


def has_view(c: dict) -> bool:
    return any(v["for_role"] == ROLE for v in c["views"])


def add_view(cat: Catalog, c: dict, by: str) -> dict:
    """A new version with the IT view. A view is configuration over the contract: the asset count stays."""
    if has_view(c):
        raise ValueError("The IT view is already in the contract.")
    c2 = dict(c)
    c2["views"] = [*c["views"], dict(VIEW)]
    saved = contracts.save(c2, by, f"added the IT view for the {ROLE}")
    open_refresh_handovers(cat, saved)
    return saved


def open_refresh_handovers(cat: Catalog, c: dict) -> list[str]:
    """One handover per failed model behind the contract, from the contract's role to the Semantic Model Owner."""
    made = []
    action = "fix the refresh or block reuse of the model"
    assert ROLE in ontology_by_role()
    for m in models_behind(cat, c):
        if m["refresh"] != "failed" or handovers._live(c["contract_id"], m["model_id"]):
            continue
        hid = handovers._next_id()
        fed = ", ".join(a["asset_id"] for a in m["scope_assets"])
        body = {
            "handover_id": hid, "contract_id": c["contract_id"], "contract_version": c["version"], "kind": "refresh",
            "from_role": c["role"], "to_role": ROLE, "pair": m["model_id"],
            "trigger": {"model_id": m["model_id"], "refresh_status": m["refresh"]},
            "evidence_snapshot": [{"sheet": "Semantic_Models", "ids": [m["model_id"]], "claim": f"refresh_status {m['refresh']}"},
                                  {"sheet": "Lineage_Edges", "ids": [f"{m['model_id']}→{a['asset_id']}" for a in m["scope_assets"]], "claim": f"feeds {fed}"}],
            "requested_action": action, "response_window_hours": handovers.RESPONSE_HOURS, "system_of_record": SYSTEM_OF_RECORD,
            "observed_blocker": f"{m['model_id']} refresh {m['refresh']}; it feeds {fed}", "suspected_cause": "",
        }
        with store.conn() as db:
            db.execute("INSERT INTO handovers VALUES (?,?,?,?,?,?)", (hid, c["contract_id"], m["model_id"], json.dumps(body), "open", store.now()))
            db.execute("INSERT INTO transitions (handover_id,to_state,by,at,reason) VALUES (?,?,?,?,?)",
                       (hid, "open", f"system on behalf of {c['role']}", store.now(), "refresh failed on a model behind the contract"))
        store.audit(f"system on behalf of {c['role']}", "handover_opened", hid, f"{m['model_id']} refresh failed, it feeds {fed}")
        made.append(hid)
    return made


def view_model(cat: Catalog, c: dict) -> dict:
    ms = models_behind(cat, c)
    healthy = sum(1 for m in ms if m["refresh"] == "healthy")
    wide = catalog_wide(cat)
    hs = [h for h in handovers.all_for(c["contract_id"]) if ROLE in (h["from_role"], h["to_role"])]
    fed_all = sorted({a for m in ms for a in m["fed"]})
    d = healthy - len(ms)
    return {"models": ms, "kpi": {"ac": healthy, "pl": len(ms), "d": d, "pct": (d / len(ms) * 100) if ms else 0, "fav": d >= 0},
            "wide": wide, "handovers": hs, "counters": handovers.counters(c["contract_id"], ROLE), "fed_all": fed_all,
            "scope_assets": list(scope_assets(cat, c).index), "onto": ontology_by_role()[ROLE]}
