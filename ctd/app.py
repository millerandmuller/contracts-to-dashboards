"""FastAPI app. Six pages, one per demo beat. State lives in SQLite; reload never loses it."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import builder, contracts, gateway, handovers, interview, pbip, preserve, refuse, render, store
from .catalog import get_catalog

ROOT = Path(__file__).resolve().parent
app = FastAPI(title="Role contracts that generate dashboards")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

REQUEST = "We need a monthly supply-risk dashboard by medicine and country."
LEAD = "Supply Analytics Lead"
RECEIVER = "Launch Excellence Lead"
CID = "CON-SUPPLY-RISK"


def ctx(request: Request, beat: int, **kw) -> dict:
    return {"beat": beat, "counters": contracts.counters(), "path": request.url.path, **kw}


def html(name: str, request: Request, beat: int, **kw) -> HTMLResponse:
    return HTMLResponse(render.page(name, **ctx(request, beat, **kw)))


@app.on_event("startup")
def _warm() -> None:
    store.conn().close()
    get_catalog()


# ---- beat 1: the request and what the catalog already knows --------------------------
@app.get("/", response_class=HTMLResponse)
def home(request: Request):
    cat = get_catalog()
    a = cat.analyze(REQUEST)
    fx = gateway.fixture("interview_supply_analytics_lead.json")
    facts = "\n".join(f"- {c.text} ({c.cite.text()})" for c in a["claims"])
    ex = gateway.complete("Explain in 2 or 3 short sentences why a tenth dashboard is the wrong move.", facts, fx["cached"]["explain_cluster"])
    contradictions = [r for r in a["requests"] if False]  # filled by the catalog for requests with conflicting tags; none in this cluster
    return html("home.html", request, 1, a=a, explain=ex, contradictions=contradictions, rules=cat.rules(),
                existing=contracts.latest(CID))


# ---- beat 2: three questions ---------------------------------------------------------
@app.get("/interview", response_class=HTMLResponse)
def interview_page(request: Request, prepared: int = 0):
    answers = gateway.fixture("interview_supply_analytics_lead.json")["answers"] if prepared else {"q1": "", "q2": "", "q3": ""}
    return html("interview.html", request, 2, role=LEAD, qs=interview.questions(LEAD), answers=answers, prepared=bool(prepared),
                error=None, proposal=None, followup=None, existing=contracts.latest(CID))


@app.post("/interview", response_class=HTMLResponse)
def interview_submit(request: Request, q1: str = Form(""), q2: str = Form(""), q3: str = Form(""), prepared: int = Form(0)):
    cat = get_catalog()
    answers = {"q1": q1, "q2": q2, "q3": q3}
    base = dict(role=LEAD, qs=interview.questions(LEAD), answers=answers, prepared=bool(prepared), existing=contracts.latest(CID))
    follow = interview.ownership_check(LEAD, q1) if q1.strip() else None
    if not (q1.strip() and q2.strip() and q3.strip()):
        return html("interview.html", request, 2, **base, error="Please answer all three questions.", proposal=None, followup=None)
    try:
        a = cat.analyze(REQUEST)
        c = builder.propose(cat, LEAD, answers, a)
        errs = contracts.validate(c, cat)
    except ValueError as e:
        return html("interview.html", request, 2, **base, error=str(e), proposal=None, followup=follow)
    return html("interview.html", request, 2, **base, error=None, proposal={"c": c, "errors": errs, "yaml": contracts.to_yaml(c)}, followup=follow)


@app.post("/contract/create")
def contract_create(q1: str = Form(...), q2: str = Form(...), q3: str = Form(...)):
    cat = get_catalog()
    a = cat.analyze(REQUEST)
    try:
        c = builder.propose(cat, LEAD, {"q1": q1, "q2": q2, "q3": q3}, a)
        saved = contracts.save(c, LEAD, "created from the repeated request cluster")
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=422)
    handovers.open_from_rule(cat, saved)
    return RedirectResponse("/contract", status_code=303)


# ---- beat 3: one contract, two views, handover ------------------------------------------
def _stage(request: Request, beat: int = 3):
    cat = get_catalog()
    c = contracts.latest(CID)
    if not c:
        return None
    views = [render.view_model(cat, c, v["for_role"]) for v in c["views"]]
    return dict(c=c, views=views, versions=contracts.versions(CID), yaml=contracts.to_yaml(c), audit=store.audit_rows(8))


@app.get("/contract", response_class=HTMLResponse)
def contract_page(request: Request, fragment: int = 0):
    s = _stage(request)
    if not s:
        return RedirectResponse("/interview?prepared=1", status_code=303)
    return html("_stage.html" if fragment else "contract.html", request, 3, **s)


@app.post("/contract/v2")
def contract_v2(reason: str = Form("Added forecast accuracy as a neighbour number")):
    cat = get_catalog()
    c = contracts.latest(CID)
    fa = cat.canonical_kpi("Demand Forecast Accuracy")
    if fa in c["neighbour_kpis"]:
        return JSONResponse({"ok": False, "error": "Already in the contract."}, status_code=409)
    c2 = dict(c)
    c2["neighbour_kpis"] = [*c["neighbour_kpis"], fa]
    try:
        contracts.save(c2, LEAD, reason)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=422)
    return {"ok": True}


@app.post("/handover/{hid}/ack")
def ack(hid: str, actor: str = Form(...), note: str = Form("seen, taking it over")):
    try:
        handovers.acknowledge(hid, actor, note)
    except handovers.NotAllowed as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=403)
    return {"ok": True}


@app.post("/handover/{hid}/close")
def close(hid: str, actor: str = Form(...), reason: str = Form("")):
    try:
        handovers.close(hid, actor, reason)
    except handovers.NotAllowed as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=403)
    return {"ok": True}


# ---- beat 4: consolidation with the preservation test -----------------------------------
@app.get("/consolidate", response_class=HTMLResponse)
def consolidate(request: Request, p: str = "CONS-1"):
    c = contracts.latest(CID)
    if not c:
        return RedirectResponse("/interview?prepared=1", status_code=303)
    res = preserve.run(get_catalog(), c, p)
    return html("consolidate.html", request, 4, c=c, res=res, pid=p, proposals=preserve.config()["proposals"], audit=store.audit_rows(6))


@app.post("/consolidate/{pid}/decide")
def decide(pid: str, choice: str = Form(...), reason: str = Form(""), by: str = Form(LEAD)):
    try:
        out = preserve.decide(get_catalog(), contracts.latest(CID), pid, choice, by, reason)
    except (ValueError, KeyError) as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=422)
    return {"ok": True, "outcome": out["outcome"]}


# ---- beat 5: a role the catalog cannot serve -------------------------------------------------
@app.get("/shift", response_class=HTMLResponse)
def shift(request: Request, role: str = "Shift Team Lead", step: int = 0):
    cat = get_catalog()
    g = refuse.gaps_for(cat, role)
    fx = gateway.fixture("interview_supply_analytics_lead.json")
    facts = "\n".join(f"- {m['kind']}: {m['what']}" for m in g["missing"])
    text = gateway.complete("Say honestly, in 2 short sentences, why nothing is shown for this role.", facts, fx["cached"]["refusal_shift_team_lead"])
    roles = [r["role"] for r in interview.ontology()]
    return html("shift.html", request, 5, g=g, text=text, preview=refuse.handover_preview(role), roles=roles, role=role)


# ---- beat 6: export -------------------------------------------------------------------------------
@app.get("/export", response_class=HTMLResponse)
def export_page(request: Request):
    return html("export.html", request, 6, c=contracts.latest(CID), result=None)


@app.post("/export", response_class=HTMLResponse)
def export_run(request: Request):
    c = contracts.latest(CID)
    if not c:
        return RedirectResponse("/interview?prepared=1", status_code=303)
    res = pbip.export(get_catalog(), CID)
    store.audit(c["role"], "pbip_exported", f"{CID} v{c['version']}", f"project folder written, hash {res['sha256'][:12]}")
    root = Path(res["root"])
    report = (root / next(f for f in res["files"] if f.endswith("report.json"))).read_text()
    return html("export.html", request, 6, c=c, result=res, report=report)


@app.get("/audit", response_class=HTMLResponse)
def audit_page(request: Request):
    return html("audit.html", request, 0, rows=store.audit_rows(200))


@app.post("/reset")
def reset():
    store.reset()
    return RedirectResponse("/", status_code=303)


@app.get("/counter.json")
def counter_json():
    return contracts.counters()
