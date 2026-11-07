"""Design language: tokens, bundled fonts, the left navigation on every page, counter on every page."""
import os
import re
from pathlib import Path

os.environ["CTD_OFFLINE"] = "1"

import pytest
from fastapi.testclient import TestClient

from ctd import store
from ctd.app import app

ANS = {"q1": "Which country needs stock this month. Once a month.", "q2": "Stock below four weeks. Then I flag the medicine and country for review.", "q3": "Launch Excellence Lead"}
STATIC = Path(__file__).resolve().parent.parent / "ctd" / "static"
PAGES = ["/", "/interview", "/interview?prepared=1", "/contract", "/consolidate", "/shift", "/export", "/audit"]


@pytest.fixture()
def client():
    store.reset()
    c = TestClient(app, follow_redirects=True)
    c.post("/contract/create", data=ANS)
    return c


@pytest.mark.parametrize("path", PAGES)
def test_every_page_has_the_left_navigation_and_no_wizard_bar(client, path):
    html = client.get(path).text
    assert '<nav class="side"' in html and "beats" not in html and 'class="top"' not in html
    for label in ["Inbox", "Three questions", "Two roles, one contract", "Consolidation", "Shift Team Lead request", "Audit log", "Export to Power BI"]:
        assert label in html
    assert "SYSTEM" not in html and "After acknowledgement" not in html
    assert len(re.findall(r'<a href="[^"]*" class="on">', html)) <= 1
    assert re.search(r'id="counter"[^>]*>Assets: <b>1</b> · Views: <b>2</b>', html), "counter missing on " + path


def test_active_chapter_carries_the_marker(client):
    assert re.search(r'<a href="/audit" class="on">Audit log', client.get("/audit").text)
    assert re.search(r'<a href="/contract#views" class="on">Two roles', client.get("/contract").text)


def test_fonts_and_licence_are_served(client):
    for f in ["ibm-plex-sans-latin-400-normal.woff2", "ibm-plex-sans-latin-600-normal.woff2", "ibm-plex-mono-latin-400-normal.woff2", "ibm-plex-mono-latin-600-normal.woff2", "OFL-IBM-Plex.txt"]:
        r = client.get("/static/fonts/" + f)
        assert r.status_code == 200 and len(r.content) > 1000, f
    css = client.get("/static/tokens.css").text
    assert "IBM Plex Sans" in css and "#10384F" in css and "#0091DF" in css


def test_no_amber_and_no_shadow_in_the_stylesheets():
    for name in ["app.css", "tokens.css"]:
        css = (STATIC / name).read_text().lower()
        assert "amber" not in css and "box-shadow" not in css and "gradient" not in css.replace("repeating-linear-gradient", ""), name


def test_columns_equal_the_data_and_pl_is_the_contract_line(client):
    from ctd import contracts
    from ctd.catalog import get_catalog
    c = contracts.latest("CON-SUPPLY-RISK")
    page = client.get("/contract").text
    got = re.findall(r'class="ac(?: fc)?" data-month="([\d-]+)" data-ac="([\d.]+)" data-pl="([\d.]+)"', page)
    focus = "/".join([c["scope"]["product_id"], "CN"])
    series = get_catalog().series(c["scope"]["product_id"], "CN")
    want = [(p["month"], float(p["value"]), c["lead_kpi"]["threshold"]["amber"]) for p in series if p["value"] is not None]
    assert len(got) == 2 * len(want) and focus
    assert [(m, float(a), float(p)) for m, a, p in got[:len(want)]] == want


def test_legend_names_the_line_and_never_calls_it_a_budget(client):
    page = client.get("/contract").text
    assert "line set in the contract (answer 2), not a budget" in page
    assert not re.search(r"PL\s*[□=:·-]*\s*(plan|budget)\b|(plan|budget)\s*\(PL\)", page, re.I)


def test_three_versions_of_the_handover_row(client):
    page = client.get("/contract").text
    assert page.count('class="hrow open"') == 2 and page.count("Waiting on me: 1") == 1 and "not available to you" in page
    hid = "HO-" + page.split("HO-")[1][:12]
    client.post(f"/handover/{hid}/ack", data={"actor": "Launch Excellence Lead"})
    page = client.get("/contract").text
    assert page.count("Seen and taken over by Launch Excellence Lead") == 2 and "state shown: <b>after acknowledgement" in page


def test_old_views_stay_behind_the_switch(client, monkeypatch):
    for v, marker in [("v2", "Line set in the contract:"), ("v1", "Focus rule:")]:
        monkeypatch.setenv("CTD_VIEW", v)
        assert marker in client.get("/contract").text
