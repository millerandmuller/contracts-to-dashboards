"""Honest refusal: code finds the gap, the wording stays plain. A role without catalog support gets no rendering."""
from __future__ import annotations

from functools import lru_cache

import pandas as pd

from .catalog import PACK, Catalog
from .interview import ontology_by_role, questions

# What a decision for this kind of role needs to see in the catalog. Plain words -> column-name fragments.
NEEDS = {
    "shift": ("a view per shift", ["shift"]),
    "batch": ("batch or line level data", ["batch", "line_id", "lot"]),
    "release status": ("a release status", ["release", "released"]),
    "deviation": ("open deviations", ["deviation", "capa"]),
}


@lru_cache(maxsize=1)
def pack_columns() -> dict[str, list[str]]:
    x = pd.read_excel(PACK, sheet_name=None, nrows=0)
    return {s: [str(c).lower() for c in df.columns] for s, df in x.items()}


def gaps_for(cat: Catalog, role: str) -> dict:
    o = ontology_by_role()[role]
    cols = pack_columns()
    n_cols = sum(len(v) for v in cols.values())
    missing = []
    for key, (plain, frags) in NEEDS.items():
        if key in " ".join(o["vocabulary"] + [o["example_decision"], o["example_signal"]]).lower():
            if not any(f in c for v in cols.values() for c in v for f in frags):
                missing.append({"kind": "Missing data", "what": plain, "checked": f"{len(cols)} sheets, {n_cols} columns"})
    owner_roles = set(cat.owners.role)
    for r in o["example_escalation"]:
        if r not in owner_roles:
            missing.append({"kind": "Missing person", "what": f"nobody in the catalog holds the role {r}", "checked": "Owners"})
    return {"role": role, "supported": o["catalog_support"] != "none" and not missing, "missing": missing,
            "questions": questions(role), "ontology": o}


def handover_preview(role: str) -> dict:
    o = ontology_by_role()[role]
    to = o["example_escalation"][-1]
    return {"from_role": role, "to_role": to, "state": "open", "requested_action": ontology_by_role().get(to, {}).get("example_action", "look at it and take it over"),
            "numbers": None, "note": "This is how the handover would look. It has no numbers because the catalog has none."}
