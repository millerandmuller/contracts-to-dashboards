"""Role contract: schema, validation (code, hard), versioning, diff. No model decides here."""
from __future__ import annotations

import copy
import json

import yaml

from . import store
from .catalog import Catalog, get_catalog
from .interview import ontology_by_role

REQUIRED = ["contract_id", "role", "owner_id", "decision", "cadence", "lead_kpi", "neighbour_kpis",
            "downstream_roles", "permitted_action", "escalation_to", "escalation_when", "evidence", "views"]
CADENCES = {"monthly"}  # the pack only holds monthly data; faster rhythms are not supported by evidence


def validate(c: dict, cat: Catalog | None = None) -> list[str]:
    """Return plain-language problems. Empty list means the contract holds."""
    cat = cat or get_catalog()
    errs: list[str] = []
    errs_seen: set[str] = set()
    for k in REQUIRED:
        if k not in c or c[k] in (None, "", []):
            errs.append(f"Missing: {k}.")
    if errs:
        return errs
    state = cat.owner_state(c["owner_id"])
    if state != "active":
        errs.append(f"Owner {c['owner_id']} is {state}. Assign an active owner.")
    elif cat.owner_role(c["owner_id"]) != c["role"]:
        errs.append(f"Owner {c['owner_id']} holds the role {cat.owner_role(c['owner_id'])}, not {c['role']}.")
    kid = c["lead_kpi"]["kpi_id"]
    if kid not in cat.kpis.index:
        errs.append(f"Number {kid} is not in the KPI catalog.")
    elif cat.kpis.loc[kid, "definition_status"] != "canonical":
        errs.append(f"Number {kid} has no agreed definition ({cat.kpis.loc[kid, 'definition_status']}). Use the canonical one.")
    if len(c["neighbour_kpis"]) > 4:
        errs.append("At most four neighbour numbers.")
    for n in c["neighbour_kpis"]:
        if n not in cat.kpis.index:
            errs.append(f"Neighbour number {n} is not in the KPI catalog.")
        elif cat.kpis.loc[n, "definition_status"] != "canonical":
            errs.append(f"Neighbour number {n} has no agreed definition. Use the canonical one.")
    if c["cadence"] not in CADENCES:
        errs.append(f"'{c['cadence']}' is faster than the evidence. The catalog only has monthly data.")
    onto = ontology_by_role()
    for r in [c["role"], c["escalation_to"], *c["downstream_roles"]]:
        if r not in onto:
            errs.append(f"Role '{r}' is not in the role list.")
    if c["escalation_to"] == c["role"]:
        errs.append("You cannot hand over to yourself. Who do you call when you are stuck?")
    elif onto.get(c["escalation_to"], {}).get("catalog_support") == "none":
        errs.append(f"The {c['escalation_to']} has no data in this catalog yet, so a handover to them cannot be followed.")
    if c["escalation_to"] not in c["downstream_roles"]:
        errs.append("The role you call when stuck must be a downstream role.")
    for e in c["evidence"]:
        if not e.get("sheet") or not e.get("ids"):
            errs.append("Every evidence line needs a sheet and record IDs.")
    t = c["lead_kpi"].get("threshold", {})
    if not t or t.get("amber") is None:
        errs.append("The lead number needs a threshold.")
    elif not (0 < t["red"] < t["amber"]) or t["amber"] != t["amber"] or t["amber"] > 1e6:
        errs.append("The thresholds do not make sense. The red line must be lower than the amber line, both above zero.")
    return [e for e in errs if not (e in errs_seen or errs_seen.add(e))]


def diff(a: dict | None, b: dict) -> str:
    if a is None:
        return "created"
    out = []
    for k in sorted(set(a) | set(b)):
        if k in ("version", "change_log"):
            continue
        if a.get(k) != b.get(k):
            out.append(f"{k}: {json.dumps(a.get(k))} -> {json.dumps(b.get(k))}")
    return "; ".join(out)


def save(c: dict, by: str, reason: str) -> dict:
    errs = validate(c)
    if errs:
        raise ValueError("; ".join(errs))
    prev = latest(c["contract_id"])
    c = copy.deepcopy(c)
    c["version"] = (prev["version"] + 1) if prev else 1
    c["change_log"] = (prev["change_log"] if prev else []) + [
        {"version": c["version"], "by": by, "at": store.now(), "reason": reason}]
    with store.conn() as db:
        db.execute("INSERT INTO contracts VALUES (?,?,?,?)", (c["contract_id"], c["version"], json.dumps(c), store.now()))
    store.audit(by, "contract_saved", f"{c['contract_id']} v{c['version']}", reason, diff(prev, c))
    return c


def latest(contract_id: str, version: int | None = None) -> dict | None:
    with store.conn() as db:
        q = "SELECT body FROM contracts WHERE contract_id=? " + ("AND version=?" if version else "ORDER BY version DESC LIMIT 1")
        r = db.execute(q, (contract_id, version) if version else (contract_id,)).fetchone()
    return json.loads(r["body"]) if r else None


def versions(contract_id: str) -> list[int]:
    with store.conn() as db:
        return [r[0] for r in db.execute("SELECT version FROM contracts WHERE contract_id=? ORDER BY version", (contract_id,))]


def counters() -> dict:
    """Views are configuration over a contract. They never count as assets."""
    with store.conn() as db:
        ids = [r[0] for r in db.execute("SELECT DISTINCT contract_id FROM contracts")]
    views = sum(len(latest(i)["views"]) for i in ids)
    return {"assets": len(ids), "views": views}


def to_yaml(c: dict) -> str:
    class NoAlias(yaml.SafeDumper):
        def ignore_aliases(self, data):
            return True
    return yaml.dump(c, Dumper=NoAlias, sort_keys=False, allow_unicode=True, width=100)
