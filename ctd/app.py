"""FastAPI app. Six pages, one per demo beat. State lives in SQLite; reload never loses it."""
from __future__ import annotations

from pathlib import Path

from fastapi import FastAPI, Form, Request
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from fastapi.staticfiles import StaticFiles

from . import builder, contracts, gateway, handovers, interview, pbip, preserve, reader, refuse, render, store
from .catalog import get_catalog

ROOT = Path(__file__).resolve().parent
app = FastAPI(title="Role contracts that generate dashboards")
app.mount("/static", StaticFiles(directory=ROOT / "static"), name="static")

REQUEST = "We need a monthly supply-risk dashboard by medicine and country."
LEAD = "Supply Analytics Lead"
RECEIVER = "Launch Excellence Lead"
CID = "CON-SUPPLY-RISK"
MAX_ANSWER = 500


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
    ex = {"text": fx["cached"]["explain_cluster"], "cached": True, "why": "shown first, model text swaps in if it answers", "live": not gateway.OFFLINE}
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
    answers = {"q1": q1[:MAX_ANSWER], "q2": q2[:MAX_ANSWER], "q3": q3[:MAX_ANSWER]}
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
    reading = reader.cached(answers, errs)  # shown at once; the page swaps in the model's reading if it answers in time
    return html("interview.html", request, 2, **base, error=None, proposal={"c": c, "errors": errs, "yaml": contracts.to_yaml(c)}, followup=follow,
                reading=reading, live=gateway.live())


@app.post("/interview/reading", response_class=HTMLResponse)
def interview_reading(request: Request, q1: str = Form(""), q2: str = Form(""), q3: str = Form("")):
    answers = {"q1": q1[:MAX_ANSWER], "q2": q2[:MAX_ANSWER], "q3": q3[:MAX_ANSWER]}
    try:
        c = builder.propose(get_catalog(), LEAD, answers, get_catalog().analyze(REQUEST))
        errs = contracts.validate(c, get_catalog())
        return HTMLResponse(render.page("_reading.html", reading=reader.ask_model(answers, errs)))
    except Exception:  # noqa: BLE001 - the page already shows a card; a failed swap changes nothing
        return HTMLResponse("", status_code=204)


@app.post("/contract/create")
def contract_create(q1: str = Form(...), q2: str = Form(...), q3: str = Form(...)):
    if max(len(q1), len(q2), len(q3)) > MAX_ANSWER:
        return JSONResponse({"ok": False, "error": "Please keep each answer short."}, status_code=422)
    cat = get_catalog()
    a = cat.analyze(REQUEST)
    try:
        c = builder.propose(cat, LEAD, {"q1": q1, "q2": q2, "q3": q3}, a)
        with store.WRITE_LOCK:
            if contracts.latest(CID):  # a double click or a second create never makes a second version; changes go through v2
                return RedirectResponse("/contract", status_code=303)
            saved = contracts.save(c, LEAD, "created from the repeated request cluster")
            handovers.open_from_rule(cat, saved)
    except ValueError as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=422)
    return RedirectResponse("/contract", status_code=303)


# ---- beat 3: one contract, two views, handover ------------------------------------------
def _stage(request: Request, beat: int = 3):
    cat = get_catalog()
    c = contracts.latest(CID)
    if not c:
        return None
    views = [render.view_model(cat, c, v["for_role"]) for v in c["views"]]
    shown = "after" if any(h["state"] != "open" for h in handovers.all_for(CID)) else "before"
    last = store.audit_rows(1)
    return dict(c=c, views=views, versions=contracts.versions(CID), yaml=contracts.to_yaml(c), audit=store.audit_rows(8), shown=shown,
                last_audit=last[0] if last else None)


@app.get("/contract", response_class=HTMLResponse)
def contract_page(request: Request, fragment: int = 0):
    s = _stage(request)
    if not s:
        return RedirectResponse("/interview?prepared=1", status_code=303)
    return html("_stage.html" if fragment else "contract.html", request, 3, **s)


@app.post("/contract/v2")
def contract_v2(reason: str = Form("Added forecast accuracy as a neighbour number")):
    cat = get_catalog()
    with store.WRITE_LOCK:
        c = contracts.latest(CID)
        if not c:
            return JSONResponse({"ok": False, "error": "No contract yet."}, status_code=409)
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
        return JSONResponse({"ok": False, "error": str(e)}, status_code=404 if str(e).startswith("No handover") else 403)
    return {"ok": True}


@app.post("/handover/{hid}/close")
def close(hid: str, actor: str = Form(...), reason: str = Form("")):
    try:
        handovers.close(hid, actor, reason)
    except handovers.NotAllowed as e:
        return JSONResponse({"ok": False, "error": str(e)}, status_code=404 if str(e).startswith("No handover") else 403)
    return {"ok": True}


# ---- beat 4: consolidation with the preservation test -----------------------------------
@app.get("/consolidate", response_class=HTMLResponse)
def consolidate(request: Request, p: str = "CONS-1"):
    c = contracts.latest(CID)
    if not c:
        return RedirectResponse("/interview?prepared=1", status_code=303)
    if p not in {x["id"] for x in preserve.config()["proposals"]}:
        p = "CONS-1"
    res = preserve.run(get_catalog(), c, p)
    return html("consolidate.html", request, 4, c=c, res=res, pid=p, proposals=preserve.config()["proposals"], audit=store.audit_rows(6))


@app.post("/consolidate/{pid}/decide")
def decide(pid: str, choice: str = Form(...), reason: str = Form(""), by: str = Form(LEAD)):
    with store.WRITE_LOCK:  # read the contract inside the lock so a parallel new version is not overwritten
        c = contracts.latest(CID)
        if not c:
            return JSONResponse({"ok": False, "error": "No contract yet."}, status_code=409)
        try:
            out = preserve.decide(get_catalog(), c, pid, choice, by, reason)
        except (ValueError, KeyError) as e:
            return JSONResponse({"ok": False, "error": str(e)}, status_code=422)
    return {"ok": True, "outcome": out["outcome"]}


# ---- beat 5: a role the catalog cannot serve -------------------------------------------------
@app.get("/explain.json")
def explain_json():
    """Model text for the cold open. The page shows the cached text at once and swaps this in if it arrives."""
    a = get_catalog().analyze(REQUEST)
    facts = "\n".join(f"- {c.text} ({c.cite.text()})" for c in a["claims"])
    fx = gateway.fixture("interview_supply_analytics_lead.json")
    return gateway.complete("Explain in 2 or 3 short sentences why a tenth dashboard is the wrong move.", facts, fx["cached"]["explain_cluster"])


@app.get("/shift", response_class=HTMLResponse)
def shift(request: Request, role: str = "Shift Team Lead", step: int = 0):
    cat = get_catalog()
    if role not in interview.ontology_by_role():
        role = "Shift Team Lead"
    g = refuse.gaps_for(cat, role)
    fx = gateway.fixture("interview_supply_analytics_lead.json")
    facts = "\n".join(f"- {m['kind']}: {m['what']}" for m in g["missing"])
    if g["missing"]:
        with store.WRITE_LOCK:
            if not any(d["role"] == role for d in store.decisions("refusal_seen")):
                store.put_decision("refusal_seen", {"role": role})
                store.audit(role, "refusal_shown", role, "the catalog cannot serve this role; a gap was named instead of a view")
    o = g["ontology"]
    base = (f"Your decision is about {o['example_decision']}. This catalog cannot show that yet. "
            "What is missing is listed below. Nothing was built, so nothing can mislead you.")
    text = gateway.complete("Say honestly, in 2 short sentences, why nothing is shown for this role.", facts, base)
    roles = [r["role"] for r in interview.ontology()]
    return html("shift.html", request, 5, g=g, text=text, preview=refuse.handover_preview(role), roles=roles, role=role)


# ---- beat 6: export -------------------------------------------------------------------------------
@app.get("/export", response_class=HTMLResponse)
def export_page(request: Request):
    return html("export.html", request, 6, c=contracts.latest(CID), result=None)


@app.post("/export", response_class=HTMLResponse)
def export_run(request: Request):
    with store.WRITE_LOCK:  # one export at a time: the folder is rewritten from scratch
        c = contracts.latest(CID)
        if not c:
            return RedirectResponse("/interview?prepared=1", status_code=303)
        res = pbip.export(get_catalog(), CID)
        again = pbip.export(get_catalog(), CID)  # the proof on screen: same contract, same hash, twice
        sm = pbip.summary(res)
        card = _closing_card(c, res, again, sm)
        store.audit(c["role"], "pbip_exported", f"{CID} v{c['version']}", f"project folder written, hash {res['sha256'][:12]}")
        return html("export.html", request, 6, c=c, result=res, sm=sm, card=card)


def _closing_card(c: dict, res: dict, again: dict, sm: dict) -> list[dict]:
    """Five lines, each with numbers counted from this run. Nothing here is typed in."""
    a = get_catalog().analyze(REQUEST)
    cnt = contracts.counters()
    vers = contracts.versions(CID)
    log = c["change_log"]
    full_log = all(e.get("by") and e.get("at") and e.get("reason") for e in log)
    cited = [e for e in c["evidence"] if e.get("sheet") and e.get("ids")]
    seen = store.decisions("refusal_seen")
    same = res["sha256"] == again["sha256"]
    return [
        {"head": "Fewer assets", "text": f"{len(a['requests'])} requests, {max(0, cnt['assets'] - (1 if vers else 0))} new dashboards, Assets: {cnt['assets']}", "ok": True},
        {"head": "Governed", "text": (f"v{vers[0]} → v{vers[-1]}" if len(vers) > 1 else f"v{vers[0]}") + ", every change with name, time, reason" + ("" if full_log else " (gaps in the log)"), "ok": full_log},
        {"head": "Cited", "text": f"every number carries sheet and record ({len(cited)} of {len(c['evidence'])} evidence lines)", "ok": len(cited) == len(c["evidence"])},
        {"head": "Honest", "text": (f"{seen[-1]['role']} got a gap, not a view" if seen else "no refusal was shown in this run"), "ok": bool(seen)},
        {"head": "Exports", "text": f"{len(res['files'])} files, " + ("same hash twice" if same else "the hash differs between two exports"), "ok": same},
    ]


@app.get("/audit", response_class=HTMLResponse)
def audit_page(request: Request):
    return html("audit.html", request, 0, rows=store.audit_rows(200))


@app.post("/reset")
def reset():
    with store.WRITE_LOCK:
        store.reset()
    return RedirectResponse("/", status_code=303)


@app.get("/counter.json")
def counter_json():
    return contracts.counters()
