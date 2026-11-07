"""IT view: a third view over the same contract. Assets stay at one; a failed refresh opens a handover only the model owner can take over."""
import os

os.environ["CTD_OFFLINE"] = "1"

import pytest
from fastapi.testclient import TestClient

from ctd import contracts, handovers, it, store
from ctd.app import app
from ctd.catalog import get_catalog

ANS = {"q1": "Which country needs stock this month. Once a month.", "q2": "Stock below four weeks. Then I flag the medicine and country for review.", "q3": "Launch Excellence Lead"}


@pytest.fixture()
def client():
    store.reset()
    c = TestClient(app, follow_redirects=False)
    c.post("/contract/create", data=ANS)
    return c


def test_adding_the_it_view_keeps_one_asset_and_makes_three_views(client):
    assert contracts.counters() == {"assets": 1, "views": 2}
    assert client.post("/contract/it-view").json() == {"ok": True}
    assert contracts.counters() == {"assets": 1, "views": 3}
    assert contracts.versions("CON-SUPPLY-RISK") == [1, 2]
    assert client.post("/contract/it-view").status_code == 409  # a second click never makes v3
    assert contracts.versions("CON-SUPPLY-RISK") == [1, 2]


def test_models_behind_the_contract_come_from_lineage(client):
    c = contracts.latest("CON-SUPPLY-RISK")
    ms = it.models_behind(get_catalog(), c)
    assert [(m["model_id"], m["refresh"]) for m in ms] == [("SM-043", "failed"), ("SM-019", "warning"), ("SM-016", "healthy"), ("SM-040", "healthy"), ("SM-065", "healthy")]
    le = get_catalog().s["Lineage_Edges"]
    for m in ms:
        for a in m["scope_assets"]:
            assert ((le.source_id == m["model_id"]) & (le.target_id == a["asset_id"]) & (le.edge_type == "MODEL_TO_ASSET")).any()
    w = it.catalog_wide(get_catalog())
    assert (w["failed"], w["failed_certified"]) == (14, 3)


def test_failed_refresh_opens_a_handover_only_the_model_owner_can_acknowledge(client):
    client.post("/contract/it-view")
    hs = [h for h in handovers.all_for("CON-SUPPLY-RISK") if h.get("kind") == "refresh"]
    assert [(h["pair"], h["from_role"], h["to_role"], h["state"]) for h in hs] == [("SM-043", "Supply Analytics Lead", "Semantic Model Owner", "open")]
    hid = hs[0]["handover_id"]
    assert client.post(f"/handover/{hid}/ack", data={"actor": "Supply Analytics Lead"}).status_code == 403
    assert client.post(f"/handover/{hid}/ack", data={"actor": "Semantic Model Owner"}).status_code == 200
    page = client.get("/it").text
    assert "Seen and taken over by Semantic Model Owner" in page and "Views: <b>3</b>" in page


def test_role_views_page_still_draws_two_cards_and_before_state(client):
    client.post("/contract/it-view")
    hid = next(h["handover_id"] for h in handovers.all_for("CON-SUPPLY-RISK") if h.get("kind") == "refresh")
    client.post(f"/handover/{hid}/ack", data={"actor": "Semantic Model Owner"})
    page = client.get("/contract?fragment=1").text
    assert page.count('<section class="card"') == 2
    assert "state shown: <b>before acknowledgement</b>" in page  # only the stock handover decides before/after
