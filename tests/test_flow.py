"""End-to-end flow through the real app: request -> questions -> contract -> handover -> consolidation -> shift lead -> export."""
import os
import sqlite3

os.environ["CTD_OFFLINE"] = "1"

import pytest
from fastapi.testclient import TestClient

from ctd import contracts, preserve, store
from ctd.app import app
from ctd.catalog import get_catalog

ANS = {"q1": "Which country needs stock this month. Once a month.", "q2": "Stock below four weeks. Then I flag the medicine and country for review.", "q3": "Launch Excellence Lead"}


@pytest.fixture()
def client():
    store.reset()
    return TestClient(app, follow_redirects=False)


def test_cold_open_numbers_come_from_the_pack(client):
    a = get_catalog().analyze("We need a monthly supply-risk dashboard by medicine and country.")
    assert len(a["requests"]) == 9
    assert len(a["assets"]) == 6 and len(a["covered_kpis"]) == 3
    assert len(a["stale_owners"]) == 2
    assert all(c.cite.ids is not None for c in a["claims"])


def test_full_flow_keeps_asset_count_at_one(client):
    r = client.post("/contract/create", data=ANS)
    assert r.status_code == 303
    assert contracts.counters() == {"assets": 1, "views": 2}
    page = client.get("/contract").text
    assert "Waiting on me" in page and "Acknowledge" in page
    hid = client.get("/audit").text.split("HO-")[1][:12]
    hid = "HO-" + hid
    # the sender cannot acknowledge
    bad = client.post(f"/handover/{hid}/ack", data={"actor": "Supply Analytics Lead"})
    assert bad.status_code == 403
    ok = client.post(f"/handover/{hid}/ack", data={"actor": "Launch Excellence Lead"})
    assert ok.status_code == 200
    assert "Seen and taken over by Launch Excellence Lead" in client.get("/contract").text
    # a new version: both views follow, asset count stays
    assert client.post("/contract/v2", data={"reason": "add forecast accuracy"}).status_code == 200
    assert contracts.counters() == {"assets": 1, "views": 2}
    assert contracts.versions("CON-SUPPLY-RISK") == [1, 2]


def test_preservation_test_blocks_denominator_flip_and_logs_human_decision(client):
    client.post("/contract/create", data=ANS)
    page = client.get("/consolidate").text
    assert "Blocked." in page and "hides an open handover" in page
    assert client.post("/consolidate/CONS-1/decide", data={"choice": "keep_exception", "reason": ""}).status_code == 422
    assert client.post("/consolidate/CONS-1/decide", data={"choice": "keep_exception", "reason": "keep until owners agree"}).status_code == 200
    assert any(a["action"] == "consolidation_blocked_exception_kept" for a in store.audit_rows())
    assert client.post("/consolidate/CONS-1/decide", data={"choice": "approve", "reason": "x"}).status_code == 422
    assert "Passed." in client.get("/consolidate?p=CONS-2").text


def test_shift_lead_gets_questions_then_named_gap_and_no_rendering(client):
    page = client.get("/shift").text
    assert "whether the line runs as planned today" in page
    assert "Nothing was built." in page and "a view per shift" in page and "<svg" not in page.split("<main>")[1]


def test_inactive_owner_contract_is_rejected():
    c = contracts.latest("x") or None
    cat = get_catalog()
    from ctd import builder
    a = cat.analyze("We need a monthly supply-risk dashboard by medicine and country.")
    bad = builder.propose(cat, "Supply Analytics Lead", ANS, a)
    bad["owner_id"] = "OWN-038"  # inactive in the pack
    assert any("inactive" in e and "active owner" in e for e in contracts.validate(bad, cat))
    bad2 = builder.propose(cat, "Supply Analytics Lead", ANS, a)
    bad2["cadence"] = "daily"
    assert any("faster than the evidence" in e for e in contracts.validate(bad2, cat))


def test_audit_log_is_append_only(client):
    client.post("/contract/create", data=ANS)
    with store.conn() as c:
        with pytest.raises(sqlite3.DatabaseError):
            c.execute("UPDATE audit SET reason='x'")
        with pytest.raises(sqlite3.DatabaseError):
            c.execute("DELETE FROM audit")


def test_pbip_export_is_reproducible(client):
    from ctd import pbip
    client.post("/contract/create", data=ANS)
    a = pbip.export(get_catalog(), "CON-SUPPLY-RISK")
    b = pbip.export(get_catalog(), "CON-SUPPLY-RISK")
    assert a["sha256"] == b["sha256"]
    assert client.post("/export").status_code == 200
