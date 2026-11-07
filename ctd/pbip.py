"""PBIP export: a Power BI project folder generated from the contract. Deterministic, no model, no timestamps.

Status: generated from the published PBIP/PBIR/TMDL structure. NOT yet opened in Power BI Desktop.
"""
from __future__ import annotations

import csv
import hashlib
import json
import shutil
from pathlib import Path

from . import contracts
from .builder import KPI_SOURCE
from .catalog import Catalog
from .evaluate import kpi_name

OUT = Path(__file__).resolve().parent.parent / "out"
SCHEMA = "https://developer.microsoft.com/json-schemas/fabric/item/report/definition"


def _id(*parts: str) -> str:
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:20]


def _write(p: Path, text: str) -> None:
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text if text.endswith("\n") else text + "\n", encoding="utf-8", newline="\n")


def _json(p: Path, obj: dict) -> None:
    _write(p, json.dumps(obj, indent=2, sort_keys=True, ensure_ascii=False))


def export(cat: Catalog, contract_id: str, version: int | None = None) -> dict:
    c = contracts.latest(contract_id, version)
    name = f"{c['contract_id']}-v{c['version']}"
    root = OUT / name
    if root.exists():
        shutil.rmtree(root)
    sm, rp = root / f"{name}.SemanticModel", root / f"{name}.Report"

    # data the model reads: ten monthly values per watched pair, long format
    lead = kpi_name(cat, c["lead_kpi"]["kpi_id"])
    cols = [("lead", lead, KPI_SOURCE[lead]["col"])] + [("neighbour", kpi_name(cat, k), KPI_SOURCE[kpi_name(cat, k)]["col"])
                                                          for k in c["neighbour_kpis"] if KPI_SOURCE[kpi_name(cat, k)]["series"]]
    rows = []
    for m in c["scope"]["markets"]:
        for role_, nm, col in cols:
            for p in cat.series(c["scope"]["product_id"], m, col):
                rows.append([c["scope"]["product_name"], cat.markets.loc[m, "market_name"], p["month"] + "-01", nm, role_,
                             "" if p["value"] is None else p["value"], p["kind"]])
    data = root / "data" / "series.csv"
    data.parent.mkdir(parents=True, exist_ok=True)
    with data.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f, lineterminator="\n")
        w.writerow(["Product", "Market", "Month", "Measure", "Role", "Value", "ValueKind"])
        w.writerows(rows)
    # the files carry version, name and reason of each change but no clock time: the time lives in the database and the audit log,
    # so the same contract version always gives the same bytes, also after a reset and a new run
    portable = {**c, "change_log": [{k: v for k, v in e.items() if k != "at"} for e in c.get("change_log", [])]}
    _json(root / "data" / "contract.json", portable)
    _write(root / "data" / "contract.yaml", contracts.to_yaml(portable))

    _json(root / f"{name}.pbip", {"$schema": "https://developer.microsoft.com/json-schemas/fabric/pbip/pbipProperties/1.0.0/schema.json",
                                  "version": "1.0", "artifacts": [{"report": {"path": f"{name}.Report"}}], "settings": {"enableAutoRecovery": True}})
    _json(rp / "definition.pbir", {"$schema": "https://developer.microsoft.com/json-schemas/fabric/item/report/definitionProperties/2.0.0/schema.json",
                                   "version": "4.0", "datasetReference": {"byPath": {"path": f"../{name}.SemanticModel"}}})
    _json(sm / "definition.pbism", {"$schema": "https://developer.microsoft.com/json-schemas/fabric/item/semanticModel/definitionProperties/1.0.0/schema.json",
                                    "version": "4.0", "settings": {}})
    _write(sm / "definition" / "database.tmdl", "database\n\tcompatibilityLevel: 1567\n")
    _write(sm / "definition" / "model.tmdl",
           "model Model\n\tculture: en-US\n\tdefaultPowerBIDataSourceVersion: powerBI_V3\n\n"
           "ref table Series\nref table Contract\nref expression DataFolder\n")
    _write(sm / "definition" / "expressions.tmdl",
           'expression DataFolder = "data" meta [IsParameterQuery=true, Type="Text", IsParameterQueryRequired=true]\n'
           "\tlineageTag: " + _id(name, "expr") + "\n")
    _write(sm / "definition" / "tables" / "Series.tmdl",
           "table Series\n\tlineageTag: " + _id(name, "series") + "\n\n"
           "\tmeasure 'Latest Value' = CALCULATE(MAX(Series[Value]), LASTDATE(Series[Month]))\n\t\tformatString: 0.0\n\n"
           "\tmeasure 'Amber Line' = " + str(c["lead_kpi"]["threshold"]["amber"]) + "\n\t\tformatString: 0.0\n\n"
           "\tcolumn Product\n\t\tdataType: string\n\t\tsourceColumn: Product\n\n"
           "\tcolumn Market\n\t\tdataType: string\n\t\tsourceColumn: Market\n\n"
           "\tcolumn Month\n\t\tdataType: dateTime\n\t\tformatString: yyyy-mm\n\t\tsourceColumn: Month\n\n"
           "\tcolumn Measure\n\t\tdataType: string\n\t\tsourceColumn: Measure\n\n"
           "\tcolumn Role\n\t\tdataType: string\n\t\tsourceColumn: Role\n\n"
           "\tcolumn Value\n\t\tdataType: double\n\t\tsourceColumn: Value\n\n"
           "\tcolumn ValueKind\n\t\tdataType: string\n\t\tsourceColumn: ValueKind\n\n"
           "\tpartition Series = m\n\t\tmode: import\n\t\tsource =\n"
           '\t\t\tlet\n\t\t\t\tSource = Csv.Document(File.Contents(DataFolder & "\\series.csv"), [Delimiter=",", Encoding=65001]),\n'
           "\t\t\t\tHeaders = Table.PromoteHeaders(Source),\n"
           '\t\t\t\tTyped = Table.TransformColumnTypes(Headers, {{"Month", type date}, {"Value", type number}}, "en-US")\n'
           "\t\t\tin\n\t\t\t\tTyped\n")
    _write(sm / "definition" / "tables" / "Contract.tmdl",
           "table Contract\n\tlineageTag: " + _id(name, "contract") + "\n\n"
           "\tcolumn Field\n\t\tdataType: string\n\t\tsourceColumn: Field\n\n\tcolumn Value\n\t\tdataType: string\n\t\tsourceColumn: Value\n\n"
           "\tpartition Contract = m\n\t\tmode: import\n\t\tsource =\n\t\t\tlet\n"
           "\t\t\t\tSource = #table({\"Field\",\"Value\"}, {" +
           ",".join('{"%s","%s"}' % (k, str(v).replace('"', "'")) for k, v in [
               ("contract_id", c["contract_id"]), ("version", c["version"]), ("role", c["role"]), ("owner_id", c["owner_id"]),
               ("decision", c["decision"]), ("lead_kpi", lead), ("amber_line", c["lead_kpi"]["threshold"]["amber"]),
               ("escalation_to", c["escalation_to"])]) + "})\n\t\t\tin\n\t\t\t\tSource\n")

    d = rp / "definition"
    _json(d / "version.json", {"$schema": f"{SCHEMA}/versionMetadata/1.0.0/schema.json", "version": "2.0.0"})
    _json(d / "report.json", {"$schema": f"{SCHEMA}/report/1.0.0/schema.json",
                              "themeCollection": {"baseTheme": {"name": "CY24SU10", "reportVersionAtImport": "5.59", "type": "SharedResources"}},
                              "layoutOptimization": "None"})
    page = _id(name, "page")
    _json(d / "pages" / "pages.json", {"$schema": f"{SCHEMA}/pagesMetadata/1.0.0/schema.json", "pageOrder": [page], "activePageName": page})
    _json(d / "pages" / page / "page.json", {"$schema": f"{SCHEMA}/page/1.0.0/schema.json", "name": page,
                                             "displayName": c["role"] + " - " + c["contract_id"], "displayOption": "FitToPage", "height": 720, "width": 1280})
    for i, (vname, vtype, x, y, wd, ht, field) in enumerate([
        ("lead_card", "card", 24, 24, 280, 120, "Latest Value"), ("lead_line", "lineChart", 24, 160, 760, 360, "Value")]):
        vid = _id(name, vname)
        _json(d / "pages" / page / "visuals" / vid / "visual.json", {
            "$schema": f"{SCHEMA}/visualContainer/1.0.0/schema.json", "name": vid,
            "position": {"x": x, "y": y, "z": i, "width": wd, "height": ht},
            "visual": {"visualType": vtype, "query": {"queryState": {"Values" if vtype == "card" else "Y": {"projections": [
                {"field": {"Measure" if vtype == "card" else "Column": {"Expression": {"SourceRef": {"Entity": "Series"}}, "Property": field}},
                 "queryRef": f"Series.{field}"}]}}}}})
    return {"name": name, "root": str(root), "files": sorted(str(p.relative_to(root)) for p in root.rglob("*") if p.is_file()),
            "sha256": tree_hash(root)}


def summary(res: dict) -> dict:
    """What the export page shows, read back from the files that were written (nothing typed into a template)."""
    import re
    root = Path(res["root"])
    name = res["name"]
    tmdl = (root / f"{name}.SemanticModel" / "definition" / "tables" / "Contract.tmdl").read_text(encoding="utf-8")
    fields = dict(re.findall(r'\{"(\w+)","([^"]*)"\}', tmdl))
    shown = {k: fields.get(k, "") for k in ("contract_id", "version", "owner_id", "amber_line", "escalation_to")}
    visual = next((p for p in (root / f"{name}.Report" / "definition" / "pages").rglob("visual.json") if '"card"' in p.read_text(encoding="utf-8")), None)
    card = json.loads(visual.read_text(encoding="utf-8"))["visual"] if visual else {}
    bound = next((pr["field"]["Measure"]["Property"] for q in card.get("query", {}).get("queryState", {}).values() for pr in q["projections"]), "")
    csv_rows = len((root / "data" / "series.csv").read_text(encoding="utf-8").splitlines()) - 1
    boxes = [("Semantic model (TMDL)", [f for f in res["files"] if f.startswith(f"{name}.SemanticModel/")]),
             ("Report (PBIR)", [f for f in res["files"] if f.startswith(f"{name}.Report/")]),
             ("data/ (contract.yaml, series.csv, %d rows)" % csv_rows, [f for f in res["files"] if f.startswith("data/")])]
    return {"fields": shown, "card_type": card.get("visualType", ""), "card_field": bound, "csv_rows": csv_rows,
            "boxes": [{"title": t, "files": fs, "count": len(fs)} for t, fs in boxes],
            "entry": [f for f in res["files"] if "/" not in f]}


def tree_hash(root: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(x for x in root.rglob("*") if x.is_file()):
        h.update(str(p.relative_to(root)).encode())
        h.update(p.read_bytes())
    return h.hexdigest()
