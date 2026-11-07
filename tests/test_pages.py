"""Inbox, contract card and consolidation after the design reference: numbers counted from the pack, rule names read from the pack."""
import os
import re
from pathlib import Path

os.environ["CTD_OFFLINE"] = "1"

import pytest
from fastapi.testclient import TestClient

from ctd import store
from ctd.app import app
from ctd.catalog import get_catalog

ANS = {"q1": "Which country needs stock this month. Once a month.", "q2": "Stock below four weeks. Then I flag the medicine and country for review.", "q3": "Launch Excellence Lead"}


@pytest.fixture()
def client():
    store.reset()
    return TestClient(app, follow_redirects=False)


def figs(page: str) -> list[tuple[str, str]]:
    head = page.split('class="hfigs"')[1].split("</main>")[0]
    return re.findall(r'<div class="hl">([^<]+)</div><div class="hv[^"]*">([^<]+)', head)


def test_inbox_header_figures_are_the_pack_counts(client):
    page = client.get("/").text
    f = dict(figs(page))
    assert f == {"SAME REQUEST, AC": "9", "PL": "1", "ΔPL": "+8"}  # covered numbers and inactive owners live in the stepper
    steps = page.split('id="stepper"')[1].split("</ol>")[0]
    assert "requested 9 times since August" in steps and "6 existing assets already cover 3 of 4 numbers" in steps
    assert "2 of them belong to people who left" in steps


def test_inbox_catalog_table_counts_from_the_pack(client):
    page = client.get("/").text
    cat = get_catalog()
    no_owner = sum(1 for o in cat.assets.owner_id if cat.owner_state(o) != "active")
    assert no_owner == 86
    assert f"<td>Assets without an active owner <span class=\"fnr\">[6]</span></td><td class=\"num\"><b>{no_owner}</b>" in page
    assert "REQ-0381" not in page and "OWN-0118" not in page  # no placeholder from the design reference


def test_reuse_is_not_available_until_a_contract_exists(client):
    assert 'aria-disabled="true">Reuse existing contract' in client.get("/").text
    client.post("/contract/create", data=ANS)
    page = client.get("/").text
    assert '<a class="btn ghost" href="/contract/card">Reuse existing contract</a>' in page


def test_contract_card_shows_every_version_and_the_evidence(client):
    assert client.get("/contract/card").status_code == 303
    client.post("/contract/create", data=ANS)
    client.post("/contract/v2")
    page = client.get("/contract/card").text
    assert "CON-SUPPLY-RISK v2" in page
    assert page.count('<td class="mono">v1</td>') == 1 and page.count('<td class="mono">v2</td>') == 1
    for sheet in ("Business_Requests", "Analytics_Assets", "KPI_Catalog", "Product_Market_Monthly", "Owners"):
        assert f'<td class="mono">{sheet}</td>' in page
    assert "configuration only, not assets" in page


def test_consolidation_caption_names_the_pack_rules(client):
    client.post("/contract/create", data=ANS)
    page = client.get("/consolidate").text
    rules = get_catalog().rules()
    cap = page.split("data-caption>")[1].split("</div>")[0]
    assert cap == (f"RULE-001 {rules['RULE-001']['rule_name'].lower()} · RULE-008 {rules['RULE-008']['rule_name'].lower()}: cite the evidence"
                   " · IBCS notation: AC actual, PL line, ΔPL difference")
    f = dict(figs(page))
    assert f["CASES REPLAYED"] == "4" and f["DECISIONS FLIPPED, AC"] == "3"
    assert "Blocked. Affected role: Launch Excellence Lead." in page


def test_export_header_defines_pl():
    from ctd import store as st
    st.reset()
    c = TestClient(app, follow_redirects=False)
    c.post("/contract/create", data={"q1": "Which country needs stock this month. Once a month.", "q2": "Stock below four weeks. Then I flag the medicine and country for review.", "q3": "Launch Excellence Lead"})
    assert "PL = no view edited by hand" in c.post("/export").text


def test_change_column_is_plain_words():
    from ctd.render import changed
    assert changed('views: [{"for_role": "A; B"}] -> []; neighbour_kpis: [] -> ["x"]') == "views, numbers next to it changed"
    assert changed("created") == "created"


def test_inbox_is_a_message_an_answer_a_stepper_and_one_button(client):
    page = client.get("/").text
    body = page.split("</nav>")[1]
    msg = body.split('id="msg"')[1].split('id="answer"')[0]
    assert "<b>From:</b> Supply Analytics Lead" in msg and "OWN-005" in msg
    assert "<b>To:</b> Data Product Owner · BI team" in msg and "OWN-004" in msg and "○</span> open" in msg
    assert "the pack does not record who asked" in msg and "REQ-0101 of 2026-10-05" in msg
    assert "A tenth dashboard? No." in body and "the first of 5 active Data Product Owners" in msg
    order = [body.index(x) for x in ('id="msg"', 'id="answer"', 'id="stepper"', "Ask the three questions", "not to the person who builds", 'id="evidence"')]
    assert order == sorted(order)
    steps = body.split('id="stepper"')[1].split("</ol>")[0]
    assert steps.count('<span class="sn">') == 6 + (0 if "in one sentence" not in steps else 1)
    assert "cached" not in body.split('id="evidence"')[0]  # no cached explanation paragraph above the evidence


def test_show_records_toggles_are_closed_and_hold_the_ids(client):
    page = client.get("/").text
    toggles = re.findall(r'<details class="recs"( open)? data-toggle="(\w+)">(.*?)</details>', page, re.S)
    assert [t[1] for t in toggles] == ["requests", "assets", "owners"] and not any(t[0] for t in toggles)
    a = get_catalog().analyze("We need a monthly supply-risk dashboard by medicine and country.")
    req, ast, own = (t[2] for t in toggles)
    assert all(r["request_id"] in req for r in a["requests"]) and "Business_Requests" in req
    assert all(x["asset_id"] in ast for x in a["assets"]) and "Analytics_Assets" in ast
    assert all(s["owner_id"] in own for s in a["stale_owners"]) and "sheet Owners" in own


def test_evidence_section_is_closed_and_keeps_every_footnote(client):
    page = client.get("/").text
    ev = page.split('<details class="evidence" id="evidence">')[1].split('<div class="nextbar"')[0]
    assert ev.startswith("<summary>Evidence and sources</summary>")
    for s in ("Assets without an active owner", "Numbers with more than one definition", "RULE-001", "RULE-008", "[4] contract store", "numbers covered", "inactive owners", "Instead of a tenth dashboard"):
        assert s in ev, s


def test_ordinals_read_as_spoken():
    from ctd.render import ordinal
    assert [ordinal(n) for n in (1, 2, 10, 20, 21, 22, 23, 101, 111, 112)] == ["first", "second", "tenth", "twentieth", "21st", "22nd", "23rd", "101st", "111th", "112th"]


def test_the_model_row_stays_hidden_until_the_model_answers():
    css = (Path(__file__).resolve().parent.parent / "ctd" / "static" / "tokens.css").read_text()
    assert "ol.stepper>li[hidden]{display:none}" in css  # display:grid on the rows would otherwise beat the hidden attribute


def test_inbox_survives_a_role_without_an_active_owner(client, monkeypatch):
    cat = get_catalog()
    owners = cat.owners.copy()
    monkeypatch.setattr(cat, "owners", owners.assign(status=owners.status.where(owners.role != "Data Product Owner", "inactive")))
    r = client.get("/")
    assert r.status_code == 200 and "Data Product Owner · BI team <span class=\"mono ctxc\">no active owner</span>" in r.text


def test_pages_survive_a_sender_without_an_active_owner(client, monkeypatch):
    cat = get_catalog()
    owners = cat.owners.copy()
    monkeypatch.setattr(cat, "owners", owners.assign(status=owners.status.where(owners.role != "Supply Analytics Lead", "inactive")))
    for path in ("/", "/interview", "/audit"):
        assert client.get(path).status_code == 200, path


def test_each_example_line_fills_its_own_field_without_a_request(client):
    from ctd import interview
    page = client.get("/interview").text
    for q in interview.questions("Supply Analytics Lead"):
        b = re.search(r'<button type="button" class="exfill" data-for="%s" data-text="([^"]*)"' % q["id"], page)
        assert b and b.group(1) == str(q["example"]).replace("&", "&amp;") and f'id="{q["id"]}" name="{q["id"]}" value=""' in page
    js = (Path(__file__).resolve().parent.parent / "ctd" / "static" / "app.js").read_text()
    fill = js.split("button.exfill")[1].split("});")[0]
    assert "f.value=b.dataset.text" in fill and "fetch" not in fill and "submit" not in fill
    assert 'href="/interview?prepared=1" title="Loads the example answers for this role. Typing works the same.">Fill in with example answers</a>' in page


def test_save_button_names_the_version_the_click_really_gives(client):
    page = client.post("/interview", data=ANS).text
    assert "Save as version 1 and render the views" in page
    client.post("/contract/create", data=ANS)
    client.post("/contract/v2")
    page = client.post("/interview", data=ANS).text
    assert "Open version 2, already saved" in page and "Save as version" not in page
    assert client.post("/contract/create", data=ANS).headers["location"] == "/contract"
    from ctd import contracts
    assert contracts.versions("CON-SUPPLY-RISK") == [1, 2]  # the click made no new version, as the label says


def test_counter_reads_built_here_until_the_first_contract_exists(client):
    page = client.get("/").text
    assert "Built here: <b>0</b> · Views: <b>0</b>" in page and "Assets: <b>0</b>" not in page
    client.post("/contract/create", data=ANS)
    assert "Assets: <b>1</b> · Views: <b>2</b>" in client.get("/contract").text
