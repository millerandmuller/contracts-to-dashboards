"""Catalog: loads the data pack, finds repeated requests, checks owners, cites every claim.

Retrieval finds; it never decides. Every statement returned here carries (sheet, ids).
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import pandas as pd

DATA = Path(__file__).resolve().parent.parent / "data"
PACK = DATA / "datapack.xlsx"

SHEETS = [
    "Products", "Markets", "Owners", "KPI_Catalog", "Analytics_Assets", "Asset_KPI_Map",
    "Product_Market_Monthly", "Supply_Operations", "Business_Requests", "Governance_Rules",
    "Governance_Signals", "Data_Quality", "Semantic_Models", "Lineage_Edges",
]

# Request text -> the canonical KPIs the dashboard would need. Keyword table, not a model.
TOPICS = {
    "supply-risk": {
        "keywords": {"supply", "risk", "inventory", "stock", "shortage"},
        "domain": "Supply Chain",
        "canonical": ["Inventory Coverage", "On-Time In-Full", "Units Shipped", "Demand Forecast Accuracy"],
    },
}
STOP = {"a", "the", "by", "and", "we", "need", "to", "for", "our", "i", "please", "new", "of", "in", "on"}


@dataclass
class Cite:
    sheet: str
    ids: list[str]

    def text(self) -> str:
        shown = ", ".join(self.ids[:6]) + (f" +{len(self.ids) - 6}" if len(self.ids) > 6 else "")
        return f"{self.sheet}: {shown}"


@dataclass
class Claim:
    text: str
    cite: Cite
    extra: dict = field(default_factory=dict)


def tokens(s: str) -> set[str]:
    return {t for t in re.findall(r"[a-z0-9]+", s.lower().replace("-", " ")) if t not in STOP}


class Catalog:
    def __init__(self, path: Path = PACK):
        self.s = {n: pd.read_excel(path, sheet_name=n) for n in SHEETS}
        self.owners = self.s["Owners"].set_index("owner_id")
        self.kpis = self.s["KPI_Catalog"].set_index("kpi_id")
        self.assets = self.s["Analytics_Assets"].set_index("asset_id")
        self.pm = self.s["Product_Market_Monthly"].sort_values("month")
        self.products = self.s["Products"].set_index("product_id")
        self.markets = self.s["Markets"].set_index("market_id")

    # ---- owners -------------------------------------------------------
    def owner_state(self, owner_id) -> str:
        if owner_id is None or (isinstance(owner_id, float) and pd.isna(owner_id)):
            return "missing"
        if owner_id not in self.owners.index:
            return "missing"
        return str(self.owners.loc[owner_id, "status"])

    def owner_role(self, owner_id) -> str | None:
        return str(self.owners.loc[owner_id, "role"]) if owner_id in self.owners.index else None

    # ---- requests ------------------------------------------------------
    def cluster(self, text: str, threshold: float = 0.85) -> pd.DataFrame:
        want = tokens(text)
        br = self.s["Business_Requests"]
        def sim(t):
            have = tokens(t)
            return len(want & have) / max(1, len(want | have))
        scores = br.request_text.map(sim)
        return br[scores >= threshold].sort_values("request_date")

    def topic_for(self, text: str) -> dict | None:
        for name, t in TOPICS.items():
            if len(tokens(text) & t["keywords"]) >= 1:
                return {"name": name, **t}
        return None

    def canonical_kpi(self, name: str) -> str:
        k = self.kpis
        hit = k[(k.canonical_metric == name) & (k.definition_status == "canonical")]
        return str(hit.index[0])

    # ---- coverage -----------------------------------------------------
    def covering_assets(self, text: str) -> dict:
        """Assets that already carry the canonical KPIs of this request.

        Rule (visible in the UI): lifecycle active, name says supply or inventory,
        carries one of the needed canonical KPIs as its primary KPI.
        """
        topic = self.topic_for(text)
        if not topic:
            return {"topic": None, "needed": [], "covered": {}, "assets": self.assets.iloc[0:0]}
        needed = [self.canonical_kpi(n) for n in topic["canonical"]]
        m = self.s["Asset_KPI_Map"]
        m = m[m.kpi_id.isin(needed) & (m.usage_role == "primary")]
        a = self.assets[self.assets.index.isin(m.asset_id)]
        a = a[(a.lifecycle_status == "active") & a.asset_name.str.contains("Supply|Inventory", case=False)]
        covered = {k: sorted(m[(m.kpi_id == k) & m.asset_id.isin(a.index)].asset_id) for k in needed}
        a = a.assign(owner_state=[self.owner_state(o) for o in a.owner_id],
                     owner_role=[self.owner_role(o) for o in a.owner_id])
        return {"topic": topic, "needed": needed, "covered": covered, "assets": a}

    def analyze(self, text: str) -> dict:
        c = self.cluster(text)
        cov = self.covering_assets(text)
        a = cov["assets"]
        covered_kpis = [k for k, v in cov["covered"].items() if v]
        stale = a[a.owner_state != "active"]
        out = {
            "request_text": text,
            "requests": c[["request_id", "request_date", "product_context", "market_context", "status"]].astype({"request_date": str}).assign(request_date=lambda d: d.request_date.str[:10]).to_dict("records"),
            "needed": cov["needed"],
            "covered_kpis": covered_kpis,
            "uncovered_kpis": [k for k in cov["needed"] if k not in covered_kpis],
            "assets": [
                {"asset_id": i, "name": r.asset_name, "product": r.product_scope, "owner_id": r.owner_id,
                 "owner_state": r.owner_state, "owner_role": r.owner_role, "audience": int(r.estimated_audience)}
                for i, r in a.iterrows()
            ],
            "stale_owners": [{"asset_id": i, "owner_id": r.owner_id, "state": r.owner_state} for i, r in stale.iterrows()],
            "first_time": len(c) == 0,
        }
        out["claims"] = [
            Claim(f"This request arrived {len(c)} times since {c.request_date.min():%B}.",
                  Cite("Business_Requests", list(c.request_id))) if len(c) else
            Claim("First time this is asked.", Cite("Business_Requests", [])),
            Claim(f"{len(a)} existing assets already carry {len(covered_kpis)} of the {len(cov['needed'])} numbers needed.",
                  Cite("Analytics_Assets", list(a.index))),
            Claim(f"{len(stale)} of them belong to people who left or are inactive.",
                  Cite("Owners", [str(o) for o in stale.owner_id])),
        ]
        return out

    def search(self, text: str, n: int = 5) -> list[dict]:
        """Lexical retrieval over KPI names, asset names, request texts. Finds, never decides."""
        want = tokens(text)
        rows = []
        for sheet, idcol, col in [("KPI_Catalog", "kpi_id", "kpi_name"), ("Analytics_Assets", "asset_id", "asset_name"),
                                  ("Business_Requests", "request_id", "request_text")]:
            df = self.s[sheet]
            for rid, t in zip(df[idcol], df[col]):
                have = tokens(str(t))
                if want & have:
                    rows.append({"sheet": sheet, "id": rid, "text": t, "score": len(want & have) / len(want | have)})
        return sorted(rows, key=lambda r: -r["score"])[:n]

    # ---- series ---------------------------------------------------------
    def series(self, product_id: str, market_id: str, col: str = "inventory_weeks_syn") -> list[dict]:
        """Ten months. Months with no shipments are gaps (not on market), never zeros, never interpolated."""
        g = self.pm[(self.pm.product_id == product_id) & (self.pm.market_id == market_id)]
        out = []
        for _, r in g.iterrows():
            gap = r.units_shipped_syn == 0 and r.inventory_weeks_syn == 0
            out.append({"month": f"{r.month:%Y-%m}", "value": None if gap else float(r[col]),
                        "units": int(r.units_shipped_syn), "kind": str(r.value_type).replace("_synthetic", "")})
        return out

    def pair_ids(self, product_id: str, market_id: str) -> list[str]:
        return [f"{product_id}/{market_id}"]

    def market_ids_for(self, region_or_market: str) -> list[str]:
        if region_or_market in self.markets.index:
            return [region_or_market]
        m = self.markets[self.markets.region == region_or_market]
        return list(m.index)

    def rules(self) -> dict:
        return self.s["Governance_Rules"].set_index("rule_id").to_dict("index")


@lru_cache(maxsize=1)
def get_catalog() -> Catalog:
    return Catalog()
