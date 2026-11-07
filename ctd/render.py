"""Render: one dense page per contract, deterministic. Sparklines are server-side SVG. No model involved."""
from __future__ import annotations

import math
import os
import re
from datetime import datetime
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


MONTH_LETTER = "JFMAMJJASOND"


def columns(series: list[dict], pl: float, uid: str = "c", slot: int = 26, up: int = 60, down: int = 34, scale_max: float = 10.0,
            bad_above: bool = False, dspan: float = 6.0) -> Markup:
    """AC over PL columns, one slot per month, and the dPL bars under them with a zero line. Boxes only, no chart library.
    PL is the line set in the contract. Months the data pack marks forecast-like are hatched. Values are never interpolated."""
    top = max([scale_max, pl] + [p["value"] for p in series if p["value"] is not None])
    k = up / top
    kd = (down / 2 - 2) / dspan  # dPL scale: +-dspan units fill the lane
    n = len(series)
    w, zero = n * slot, up + 4 + down / 2
    h = up + 4 + down + 14
    o = [f'<svg class="cols" width="{w}" height="{h}" viewBox="0 0 {w} {h}" role="img" aria-label="Stock cover per month, AC over PL, with the difference to PL below">',
         f'<defs><pattern id="hatch-{uid}" width="4" height="4" patternUnits="userSpaceOnUse" patternTransform="rotate(45)"><rect width="4" height="4" fill="#fff"/><line x1="0" y1="0" x2="0" y2="4" stroke="#1A1A1A" stroke-width="1.6"/></pattern></defs>']
    for i, p in enumerate(series):
        x = i * slot
        base = up
        o.append(f'<rect class="pl" x="{x + 3}" y="{base - pl * k:.1f}" width="{slot - 6}" height="{pl * k:.1f}"/>')
        v = p["value"]
        if v is not None:
            fc = p["kind"] == "forecast_like"
            key = p.get("month") or p.get("id")
            o.append(f'<rect class="ac{" fc" if fc else ""}" data-month="{key}" data-ac="{v}" data-pl="{pl}" x="{x + 7}" y="{base - v * k:.1f}" width="{slot - 14}" height="{v * k:.1f}"'
                     + (f' fill="url(#hatch-{uid})"' if fc else "") + "/>")
            d = max(-dspan, min(dspan, v - pl))
            good = v <= pl if bad_above else v >= pl
            hh = abs(d) * kd
            y = zero - hh if d >= 0 else zero
            o.append(f'<rect class="dv {"good" if good else "bad"}" data-month="{key}" data-dpl="{v - pl:.2f}" x="{x + 8}" y="{y:.1f}" width="{slot - 16}" height="{max(hh, 0.6):.1f}"/>')
        else:
            o.append(f'<text class="gap" x="{x + slot / 2}" y="{base - 4}" text-anchor="middle">–</text>')
        letter = p["letter"] if "letter" in p else MONTH_LETTER[int(p["month"][5:7]) - 1]
        o.append(f'<text class="mo" x="{x + slot / 2}" y="{h - 2}" text-anchor="middle">{letter}</text>')
    o.append(f'<line class="zero" x1="0" x2="{w}" y1="{zero}" y2="{zero}"/></svg>')
    return Markup("".join(o))


def pct_lollipop(pct: float | None, fav: bool | None = None, w: int = 100, h: int = 16, span: float = 50.0) -> Markup:
    """dPL % on a 100 px track, zero in the middle, dot at the value. Colour says good or bad, never the sign; no verdict means ink."""
    if pct is None:
        return Markup(f'<svg class="pl-lolli" width="{w}" height="{h}" role="img" aria-label="no value"><text x="2" y="{h - 4}" class="gap">no value</text></svg>')
    mid = w / 2
    x = mid + max(-span, min(span, pct)) * (mid - 3) / span
    cls = "neutral" if fav is None else ("good" if fav else "bad")
    return Markup(f'<svg class="pl-lolli {cls}" width="{w}" height="{h}" role="img" aria-label="{pct:+.0f} percent">'
                  f'<line class="trk" x1="2" x2="{w - 2}" y1="{h / 2}" y2="{h / 2}"/><line class="z" x1="{mid}" x2="{mid}" y1="2" y2="{h - 2}"/>'
                  f'<line class="stem" x1="{mid}" x2="{x:.1f}" y1="{h / 2}" y2="{h / 2}"/><circle class="dot" cx="{x:.1f}" cy="{h / 2}" r="3.4"/></svg>')


def due_text(h: dict) -> str:
    """Time left in the response window, computed from the record. Overdue is a word, never a colour change."""
    if h["state"] == "closed":
        return ""
    left = h["response_window_hours"] - (datetime.now() - datetime.fromisoformat(h["created_at"])).total_seconds() / 3600
    return f"due in {max(0, math.ceil(left))} h" if left >= 0 else f"overdue {max(1, math.ceil(-left))} h"


def view_template() -> str:
    """CTD_VIEW=v1 or v2 draws an earlier layout (plan B); the default is v3. Read per request so a switch needs no restart."""
    return {"v1": "_view.html", "v2": "_view_v2.html"}.get(os.environ.get("CTD_VIEW", "v3"), "_view_v3.html")


def change(prev: float | None, cur: float | None) -> dict | None:
    if prev is None or cur is None:
        return None
    d = cur - prev
    pct = (d / prev * 100) if prev else None
    return {"abs": d, "pct": pct, "dir": "up" if d > 0 else "down" if d < 0 else "flat",
            "bar": 0 if pct is None else min(100, round(abs(pct)))}


env.globals["sparkline"] = sparkline
env.globals["lollipop"] = lollipop
env.globals["columns"] = columns
env.globals["pct_lollipop"] = pct_lollipop
env.globals["due_text"] = due_text
env.globals["view_template"] = view_template
env.globals["rule_version"] = RULE_VERSION
env.globals["manual_edits"] = lambda: store.audit_count("view_edited")

# The demo path in its one order: (number, group, label, link, paths that count as this page). Nav and Next buttons both read it.
DEMO = [
    (1, "REQUEST", "Inbox", "/", ["/"]),
    (2, "REQUEST", "Three questions", "/interview", ["/interview"]),
    (3, "REQUEST", "Contract", "/contract/card", ["/contract/card"]),
    (4, "VIEWS", "Two roles, one contract", "/contract#views", ["/contract"]),
    (5, "VIEWS", "IT: the model behind it", "/it", ["/it"]),
    (6, "GOVERNANCE", "Consolidation", "/consolidate", ["/consolidate"]),
    (7, "GOVERNANCE", "Shift Team Lead request", "/shift", ["/shift", "/shift/curated"]),
    (8, "GOVERNANCE", "Export to Power BI", "/export", ["/export"]),
    (9, "GOVERNANCE", "Audit log", "/audit", ["/audit"]),
]
NO_CONTRACT_NEEDED = {"/", "/interview", "/shift", "/audit"}


def demo_next(path: str) -> dict | None:
    """The button at the foot of a page: the next page of the demo, or back to the Inbox from the last one."""
    i = next((k for k, d in enumerate(DEMO) if path in d[4]), None)
    if i is None:
        return None
    n = DEMO[(i + 1) % len(DEMO)]
    return {"label": n[2], "href": n[3], "back": i == len(DEMO) - 1}


WORDS = "first second third fourth fifth sixth seventh eighth ninth tenth eleventh twelfth thirteenth fourteenth fifteenth sixteenth seventeenth eighteenth nineteenth twentieth".split()


def ordinal(n: int) -> str:
    """tenth, 21st, 112th: words up to twenty as they are spoken, digits with the right suffix above."""
    if 1 <= n <= len(WORDS):
        return WORDS[n - 1]
    return f"{n}{'th' if n % 100 in (11, 12, 13) else {1: 'st', 2: 'nd', 3: 'rd'}.get(n % 10, 'th')}"


env.filters["ordinal"] = ordinal
env.globals["demo"] = DEMO
env.globals["demo_next"] = demo_next
env.globals["no_contract_needed"] = NO_CONTRACT_NEEDED
env.filters["fmt"] = lambda v, d=1: "–" if v is None else (f"{v:,.{d}f}" if isinstance(v, float) else f"{v:,}")
env.filters["signed"] = lambda v, d=0: ("−" if v < 0 else "+" if v > 0 else "±") + f"{abs(v):.{d}f}"
env.filters["hhmm"] = lambda s: s[11:16] if s else ""

_FIELD_WORDS = {"views": "views", "neighbour_kpis": "numbers next to it", "lead_kpi": "lead number", "cadence": "how often",
                "escalation_to": "hand over to", "owner_id": "owner", "evidence": "evidence", "question": "business question"}


def changed(diff: str) -> str:
    """The audit 'change' column in plain words: which fields changed, not their JSON. The full diff stays in the row's tooltip."""
    if not diff or diff == "created":
        return diff or ""
    keys = re.findall(r"(?:^|; )([a-z_]+): ", diff)
    if not keys:
        return diff
    return ", ".join(_FIELD_WORDS.get(k, k.replace("_", " ")) for k in keys) + " changed"


env.filters["changed"] = changed


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
    pl = thr["amber"]
    for n in nb:
        vals = [p["value"] for p in n["series"]]
        n["prev"] = vals[-2] if len(vals) > 1 else None
        n["pct"] = ((n["latest"] - n["prev"]) / n["prev"] * 100) if n["prev"] and n["latest"] is not None else None
    for r in rows:
        r["dpl"] = r["latest"] - pl if r["latest"] is not None else None
        r["dpl_pct"] = r["dpl"] / pl * 100 if r["dpl"] is not None else None
    for r in mine:
        r["dpl"], r["dpl_pct"] = next((x["dpl"], x["dpl_pct"]) for x in rows if x["pair"] == r["pair"])
    kpi = None
    if focus:
        kpi = {"ac": focus["latest"], "pl": pl, "d": focus["dpl"], "pct": focus["dpl_pct"], "fav": focus["dpl"] >= 0 if focus["dpl"] is not None else None,
               "below": sum(1 for r in rows if r["latest"] is not None and r["latest"] < pl), "pairs": len(rows),
               "months": f"{focus['series'][0]['month'][:4]}", "unit": thr["unit"]}
    def_note = note(f"{lname} {c['lead_kpi']['kpi_id']} · Product_Market_Monthly: {focus['pair']}/{focus['month']}") if focus else 0
    gloss = lambda r: (f"{r} (decides {onto[r]['example_decision']})" if r in onto and onto[r].get("source") == "pack" else r)  # noqa: E731
    if role == c["escalation_to"]:
        before = [{"role": c["role"], "line": (f"lowest escalated pair {focus['market_name']} {focus['latest']:.1f} wk · ΔPL {focus['dpl_pct']:+.0f} %" if focus else ""), "focus": True}]
        after = []
    else:
        before = [{"role": gloss(r), "line": "", "focus": False} for r in c.get("upstream_roles", [])]
        after = []
        for r in c["downstream_roles"]:
            open_h = [h for h in hs if h["to_role"] == r and h["state"] != "closed"]
            line = f"{open_h[0]['handover_id']} {open_h[0]['state']}" if open_h else "nothing handed over"
            if open_h and open_h[0]["state"] == "acknowledged":
                line += " · taken over " + next(t["at"][11:16] for t in open_h[0]["transitions"] if t["to_state"] == "acknowledged")
            after.append({"role": gloss(r), "line": line, "focus": bool(open_h)})
    return {"kpi": kpi, "pl": pl, "def_note": def_note, "before": before, "after": after, "role": role, "c": c, "lead_name": lname, "lead_plain": KPI_SOURCE[lname]["plain"], "thr": thr, "rows": mine,
            "all_rows": rows, "focus": focus, "neighbours": nb, "foots": foots, "counters": ctr, "handovers": hs,
            "side_roles": side_roles, "focus_rule": focus_rule, "is_receiver": role == c["escalation_to"],
            "action": c["permitted_action"], "decision": c["decision"], "onto": onto[role],
            "audit_line": audit_line, "question": question, "lead_change": focus["change"] if focus else None,
            "lead_prev": focus["prev"] if focus else None, "prev_month": _prev_month(focus["series"]) if focus else ""}


def page(name: str, **ctx) -> str:
    return env.get_template(name).render(**ctx)
