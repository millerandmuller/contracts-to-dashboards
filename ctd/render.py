"""Render: one dense page per contract, deterministic. Sparklines are server-side SVG. No model involved."""
from __future__ import annotations

import os
from pathlib import Path

from jinja2 import Environment, FileSystemLoader, select_autoescape
from markupsafe import Markup

from . import handovers
from .catalog import Catalog
from . import store
from .evaluate import RULE_VERSION, kpi_name, neighbour_values, pair_rows
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


def lollipop(prev: float | None, cur: float | None, lo: float, hi: float, amber: float, hollow: bool = False, w: int = 150, h: int = 22) -> Markup:
    """Previous month to latest month on one shared scale. The tick is the amber line. Hollow dot = forecast."""
    if cur is None:
        return Markup(f'<svg class="lolli" width="{w}" height="{h}" role="img" aria-label="no data"><text x="2" y="{h-7}" class="gap">no data</text></svg>')
    span = (hi - lo) or 1
    px = lambda v: 6 + (v - lo) / span * (w - 12)  # noqa: E731
    y = h / 2
    svg = [f'<svg class="lolli" width="{w}" height="{h}" role="img" aria-label="previous month to latest month">',
           f'<line class="thr" x1="{px(amber):.1f}" x2="{px(amber):.1f}" y1="2" y2="{h-2}"/>']
    if prev is not None:
        svg.append(f'<line class="stem" x1="{px(prev):.1f}" x2="{px(cur):.1f}" y1="{y}" y2="{y}"/>')
        svg.append(f'<circle class="prev" cx="{px(prev):.1f}" cy="{y}" r="3"/>')
    svg.append(f'<circle class="cur{" hollow" if hollow else ""}" cx="{px(cur):.1f}" cy="{y}" r="4.2"/>')
    svg.append("</svg>")
    return Markup("".join(svg))


def view_template() -> str:
    """CTD_VIEW=v1 draws the earlier layout (plan B). Read per request so a switch needs no restart."""
    return "_view.html" if os.environ.get("CTD_VIEW", "v2") == "v1" else "_view_v2.html"


def change(prev: float | None, cur: float | None) -> dict | None:
    if prev is None or cur is None:
        return None
    d = cur - prev
    pct = (d / prev * 100) if prev else None
    return {"abs": d, "pct": pct, "dir": "up" if d > 0 else "down" if d < 0 else "flat",
            "bar": 0 if pct is None else min(100, round(abs(pct)))}


env.globals["sparkline"] = sparkline
env.globals["lollipop"] = lollipop
env.globals["view_template"] = view_template
env.filters["fmt"] = lambda v, d=1: "–" if v is None else (f"{v:,.{d}f}" if isinstance(v, float) else f"{v:,}")
env.filters["hhmm"] = lambda s: s[11:16] if s else ""


def sentence(role_view: str, kpi: dict, pair: dict, thr: dict) -> str:
    """Role-language sentence. Template, not model: no invented numbers."""
    place = pair["market_name"]
    if pair["status"] == "ok":
        return f"{place}: {pair['latest']:.1f} {thr['unit']} of stock. Fine."
    since = "for 2 months in a row" if pair["escalated"] else "this month"
    return f"{place}: {pair['latest']:.1f} {thr['unit']} of stock. That is below {thr['amber']:g}, {since}."


def _prev_month(series: list[dict]) -> str:
    return series[-2]["month"] if len(series) > 1 else ""


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
        r["note"] = note(f"Product_Market_Monthly: {r['pair']}/{r['month']}" + (" (forecast)" if r["last_kind"] == "forecast_like" else ""))
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
    for r in mine:
        vals = [p["value"] for p in r["series"]]
        r["prev"] = vals[-2] if len(vals) > 1 else None
        r["change"] = change(r["prev"], r["latest"])
    pts = [x for r in mine for x in (r["prev"], r["latest"]) if x is not None] + [thr["amber"]]
    for r in mine:
        r["lolli"] = lollipop(r["prev"], r["latest"], min(pts), max(pts), thr["amber"], r["last_kind"] == "forecast_like")
    observed = (focus or (rows[0] if rows else {})).get("month", "")
    owner_state = cat.owner_state(c["owner_id"])
    manual = store.audit_count("view_edited")
    audit_line = {"contract": f"{c['contract_id']} v{c['version']}", "owner": f"{c['owner_id']} {owner_state}", "observed": observed,
                  "rule": f"v{RULE_VERSION}", "manual": "no manual edits" if manual == 0 else f"{manual} manual edits"}
    question = (c["decision"].rstrip(".?") + ", and who takes it over?") if role == c["role"] else "What has been handed to me, and what do I do with it?"
    focus_rule = next((v["focus_rule"] for v in c["views"] if v["for_role"] == role), "")
    return {"role": role, "c": c, "lead_name": lname, "lead_plain": KPI_SOURCE[lname]["plain"], "thr": thr, "rows": mine,
            "all_rows": rows, "focus": focus, "neighbours": nb, "foots": foots, "counters": ctr, "handovers": hs,
            "side_roles": side_roles, "focus_rule": focus_rule, "is_receiver": role == c["escalation_to"],
            "action": c["permitted_action"], "decision": c["decision"], "onto": onto[role],
            "audit_line": audit_line, "question": question, "lead_change": focus["change"] if focus else None,
            "lead_prev": focus["prev"] if focus else None, "prev_month": _prev_month(focus["series"]) if focus else ""}


def page(name: str, **ctx) -> str:
    return env.get_template(name).render(**ctx)
