"""Inbox, contract card and consolidation after the design reference: numbers counted from the pack, rule names read from the pack."""
import os
import re

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
    f = dict(figs(client.get("/").text))
    assert f["SAME REQUEST, AC"] == "9" and f["PL"] == "1" and f["ΔPL"] == "+8"
    assert f["NUMBERS COVERED"] == "3" and f["INACTIVE OWNERS"] == "2"


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
