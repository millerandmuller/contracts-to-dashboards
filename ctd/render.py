"""Render: one dense page per contract, deterministic. Sparklines are server-side SVG. No model involved."""
from __future__ import annotations

from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from . import handovers
from .catalog import Catalog
from .evaluate import kpi_name, neighbour_values, pair_rows
from .builder import KPI_SOURCE
from .interview import ontology_by_role

ROOT = Path(__file__).resolve().parent
env = Environment(loader=FileSystemLoader(ROOT / "templates"), autoescape=select_autoescape(["html"]))


def sparkline(series: list[dict], amber: float | None = None, w: int = 132, h: int = 30) -> Markup:
    """Line over ten months. Gaps break the line (never interpolated). Forecast-like months are hollow dots."""
    vals = [p["value"] for p in series if p["value"] is not None]
    if not vals:
        return Markup(f'<svg class="spark" width="{w}" height="{h}" role="img" aria-label="no data"><text x="2" y="{h-8}" class="gap">no data</text></svg>')
    lo, hi = min(vals + ([amber] if amber is not None else [])), max(vals + ([amber] if amber is not None else []))
    span = (hi - lo) or 1
    n = len(series)
    px = lambda i: 3 + i * (w - 6) / max(1, n - 1)  # noqa: E731
    py = lambda v: h - 4 - (v - lo) / span * (h - 8)  # noqa: E731
    parts, seg = [], []
    for i, p in enumerate(series):
        if p["value"] is None:
            if seg:
                parts.append(seg)
            seg = []
        else:
            seg.append((px(i), py(p["value"])))
    if seg:
        parts.append(seg)
    svg = [f'<svg class="spark" width="{w}" height="{h}" role="img" aria-label="ten months">']
    if amber is not None:
        svg.append(f'<line x1="0" x2="{w}" y1="{py(amber):.1f}" y2="{py(amber):.1f}" class="thr"/>')
    for seg in parts:
        if len(seg) > 1:
            svg.append('<polyline class="ln" points="' + " ".join(f"{x:.1f},{y:.1f}" for x, y in seg) + '"/>')
    for i, p in enumerate(series):
        if p["value"] is None:
            continue
        cls = "dot hollow" if p["kind"] == "forecast_like" else "dot"
        if i == n - 1:
            cls += " last"
        svg.append(f'<circle class="{cls}" cx="{px(i):.1f}" cy="{py(p["value"]):.1f}" r="{2.6 if i == n - 1 else 1.8}"/>')
    svg.append("</svg>")
    return Markup("".join(svg))


env.globals["sparkline"] = sparkline
env.filters["fmt"] = lambda v, d=1: "–" if v is None else (f"{v:,.{d}f}" if isinstance(v, float) else f"{v:,}")
env.filters["hhmm"] = lambda s: s[11:16] if s else ""


def sentence(role_view: str, kpi: dict, pair: dict, thr: dict) -> str:
    """Role-language sentence. Template, not model: no invented numbers."""
    place = pair["market_name"]
    if pair["status"] == "ok":
        return f"{place}: {pair['latest']:.1f} {thr['unit']} of stock. Fine."
    since = "for 2 months in a row" if pair["escalated"] else "this month"
    return f"{place}: {pair['latest']:.1f} {thr['unit']} of stock. That is below {thr['amber']:g}, {since}."


def view_model(cat: Catalog, c: dict, role: str) -> dict:
    thr = c["lead_kpi"]["threshold"]
    lname = kpi_name(cat, c["lead_kpi"]["kpi_id"])
    rows = pair_rows(cat, c)
    mine = [r for r in rows if r["escalated"]] if role == c["escalation_to"] else rows
    focus = mine[0] if mine else None
    foots: list[str] = []

    def note(text: str) -> int:
        if text not in foots:
            foots.append(text)
        return foots.index(text) + 1

    for r in mine:
        r["note"] = note(f"Product_Market_Monthly: {r['pair']}/{r['month']} ({r['last_kind'].replace('_', '-')})")
        r["sentence"] = sentence(role, c["lead_kpi"], r, thr)
    nb = neighbour_values(cat, c, focus["pair"]) if focus else []
    for n in nb:
        n["note"] = note(n["cite"])
    ctr = handovers.counters(c["contract_id"], role)
    hs = [h for h in handovers.all_for(c["contract_id"]) if (h["from_role"] == role or h["to_role"] == role)]
    onto = ontology_by_role()
    side_roles = [{"role": r, "kind": k} for k, rs in (("before you", c.get("upstream_roles", [])), ("after you", c["downstream_roles"])) for r in rs]
    for s in side_roles:
        s["waiting"] = sum(1 for h in handovers.all_for(c["contract_id"]) if h["state"] == "open" and h["to_role"] == s["role"])
    focus_rule = next((v["focus_rule"] for v in c["views"] if v["for_role"] == role), "")
    return {"role": role, "c": c, "lead_name": lname, "lead_plain": KPI_SOURCE[lname]["plain"], "thr": thr, "rows": mine,
            "all_rows": rows, "focus": focus, "neighbours": nb, "foots": foots, "counters": ctr, "handovers": hs,
            "side_roles": side_roles, "focus_rule": focus_rule, "is_receiver": role == c["escalation_to"],
            "action": c["permitted_action"], "decision": c["decision"], "onto": onto[role]}


def page(name: str, **ctx) -> str:
    return env.get_template(name).render(**ctx)
