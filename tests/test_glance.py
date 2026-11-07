"""F16 glance layout: folds, one framed box per handover, the acknowledgement update, the same hierarchy on /it and /shift/curated."""
import os
import re

os.environ["CTD_OFFLINE"] = "1"

import pytest
from fastapi.testclient import TestClient

from ctd import handovers, store
from ctd import shift as sd
from ctd.app import app

ANS = {"q1": "Which country needs stock this month. Once a month.", "q2": "Stock below four weeks. Then I flag the medicine and country for review.", "q3": "Launch Excellence Lead"}
RECV = "Launch Excellence Lead"
ZONE_LABELS = ["3 S", "30 S", "300 S", "A · STATUS", "C · ACT", "D · NEXT TO YOU", "B · NEXT TO THE LEAD NUMBER"]


@pytest.fixture()
def client():
    store.reset()
    c = TestClient(app, follow_redirects=False)
    c.post("/contract/create", data=ANS)
    return c


def hid_of(client) -> str:
    return handovers.all_for("CON-SUPPLY-RISK")[0]["handover_id"]


def cards(page: str) -> list[str]:
    return re.findall(r'<section class="card".*?</section>', page, re.S)


def test_each_card_has_three_folds_closed_on_load_and_nothing_is_removed(client):
    cs = cards(client.get("/contract").text)
    assert len(cs) == 2
    for c in cs:
        folds = re.findall(r'<details class="g-fold" data-fold="[^"]+"(?: open)?>', c)
        assert len(folds) == 3 and not any("open" in f for f in folds)
        for want in ["Why: by pair, against the line 4.0", "Next to the lead number", "Sources", "no line in the contract; shown as is, not compared"]:
            assert want in c
    assert cs[0].count("<tr><td>") >= 4 + 2  # four pairs and the neighbour rows are in the page, only folded


def test_zone_labels_are_gone_and_the_principle_stands_once_in_the_legend(client):
    page = client.get("/contract").text.split("<main>")[1]
    for z in ZONE_LABELS:
        assert z not in page
    assert page.count("status in 3 s, cause in 30 s, action in 300 s") == 1


def test_the_handover_is_the_only_framed_box_and_its_lines_follow_the_brief(client):
    left, right = cards(client.get("/contract").text)
    assert left.count('class="g-ho"') == 1 and "not available to you: only Launch Excellence Lead can acknowledge" in left and ">Acknowledge<" not in left
    assert 'class="g-ho on"' in right and right.count(">Acknowledge<") == 1 and "means: seen and taken over. Not done." in right
    for c in (left, right):
        assert c.count("Close with reason") == 1 and "asked: <em>" in c and "HO-2026-" in c
    assert "What do I pass on this month?" in left and "What is waiting for me?" in right


def test_acknowledgement_frames_both_boxes_and_the_fragment_carries_the_update(client):
    assert client.post(f"/handover/{hid_of(client)}/ack", data={"actor": RECV}).status_code == 200
    frag = client.get("/contract?fragment=1").text  # what refreshStage swaps in, no reload
    left, right = cards(frag)
    for c in (left, right):
        assert 'class="g-ho on" data-state="acknowledged"' in c and '<span class="shape" aria-hidden="true">◐</span> acknowledged' in c
        assert re.search(r'<p class="g-took">Seen and taken over by Launch Excellence Lead, \d\d:\d\d</p>', c)
        assert ">Acknowledge<" not in c and "not available to you" not in c and "Close with reason" in c
    assert "Waiting on me <b>0</b> · taken over by me <b>1</b>" in right
    assert "handover acknowledged" in frag and "state shown: <b>after acknowledgement</b>" in frag


def test_refresh_script_keeps_opened_folds_open():
    js = open("ctd/static/app.js").read()
    assert "details[data-fold][open]" in js and "d.open=true" in js


def test_old_layouts_stay_reachable_on_all_three_pages(client, monkeypatch):
    client.post("/shift/curated/create")
    client.post("/contract/it-view")
    monkeypatch.setenv("CTD_VIEW", "v3")
    assert "C · ACT · 300 S" in client.get("/contract").text
    assert "C · ACT · 300 S" in client.get("/it").text
    assert "C · ACT · 300 S" in client.get("/shift/curated").text


def test_it_page_is_one_glance_card_with_the_model_list_in_the_why_fold(client):
    client.post("/contract/it-view")
    page = client.get("/it").text
    main = page.split("<main>")[1]
    assert len(cards(page)) == 1 and 'class="pagehead glanceband"' in page
    assert main.count("rendered from") == 1 and main.count("Assets: <b>") == 1
    why = re.search(r'<details class="g-fold" data-fold="it-why">.*?</details>', main, re.S).group(0)
    assert "SM-043" in why and "failed" in why and "RULE-004" in why
    card = cards(page)[0]
    assert re.search(r'<div class="g-big">3<small>of 5 models', card) and 'class="g-ho on"' in card and "Waiting on me <b>1</b>" in card
    assert not any(z in main for z in ZONE_LABELS)


def test_it_acknowledgement_shows_the_took_line_and_the_taken_over_counter(client):
    client.post("/contract/it-view")
    hid = next(h["handover_id"] for h in handovers.all_for("CON-SUPPLY-RISK") if h.get("kind") == "refresh")
    client.post(f"/handover/{hid}/ack", data={"actor": "Semantic Model Owner"})
    page = client.get("/it").text
    assert "Seen and taken over by Semantic Model Owner" in page and "taken over by me <b>1</b>" in page


def test_curated_shift_page_has_two_glance_cards_and_the_badge_on_banner_cards_legend():
    store.reset()
    c = TestClient(app, follow_redirects=False)
    c.post("/shift/curated/create")
    page = c.get("/shift/curated").text
    main = page.split("<main>")[1]
    assert len(cards(page)) == 2 and main.count('class="g-top"') == 2
    assert 'class="curbanner"' in main and main.count(f'<span class="badge curated">{sd.BADGE}</span>') == 2
    assert main.split('class="closer"')[1].count(sd.BADGE) == 1
    assert main.count("rendered from contract, no manual edits") == 1
    assert not any(z in main for z in ZONE_LABELS)
    hid = handovers.all_for(sd.CID)[0]["handover_id"]
    c.post(f"/handover/{hid}/ack", data={"actor": sd.IN})
    page = c.get("/shift/curated").text
    assert page.count(f"Seen and taken over by {sd.IN}") == 2 and page.count('class="g-ho on" data-state="acknowledged"') == 2
