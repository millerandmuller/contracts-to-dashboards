"""Shift change as a handover on curated shift data. The refusal for the pack stays first; the pack's asset count never moves."""
import os
import re

os.environ["CTD_OFFLINE"] = "1"

import pandas as pd
import pytest
from fastapi.testclient import TestClient

from ctd import contracts, handovers, store
from ctd import shift as sd
from ctd.app import app

ANS = {"q1": "Which country needs stock this month. Once a month.", "q2": "Stock below four weeks. Then I flag the medicine and country for review.", "q3": "Launch Excellence Lead"}


@pytest.fixture()
def client():
    store.reset()
    return TestClient(app, follow_redirects=False)


def test_refusal_stays_first_and_offers_the_curated_run(client):
    page = client.get("/shift").text
    main = page.split("<main>")[1]
    assert main.index("Nothing was built.") < main.index("Show the same contract on curated shift data")
    assert "<svg" not in main
    assert client.get("/shift/curated").status_code == 303  # nothing to show before it is asked for


def test_curated_data_set_names_no_real_plant():
    raw = sd.CSV.read_text()
    assert "Bayer" not in raw and "Turku" not in raw
    d = pd.read_csv(sd.CSV, keep_default_na=False)
    assert len(d) == 124 and set(d.line) == {"Line A", "Line B"} and set(d["shift"]) == {"early", "late", "night"}
    assert (d.curated == sd.BADGE).all()


def test_create_is_single_and_leaves_the_pack_catalog_alone(client):
    client.post("/contract/create", data=ANS)
    before = contracts.counters()
    assert client.post("/shift/curated/create").status_code == 303
    assert client.post("/shift/curated/create").status_code == 303
    assert contracts.counters() == before == {"assets": 1, "views": 2}
    assert len(handovers.all_for(sd.CID)) == 1


def test_badge_on_every_view_and_columns_are_the_csv_values(client):
    client.post("/shift/curated/create")
    page = client.get("/shift/curated").text
    assert page.count(sd.BADGE) >= 4  # banner, two cards, legend
    d = sd.data()
    want = [float(x) for x in d[d.line == "Line B"].tail(9).downtime_min]
    got = [float(x) for x in re.findall(r'class="ac" data-month="SH-\d+" data-ac="([0-9.]+)"', page)]
    assert got == want + want  # both cards draw the same nine shifts of Line B


def test_only_the_incoming_lead_acknowledges_and_both_cards_follow(client):
    client.post("/shift/curated/create")
    hid = handovers.all_for(sd.CID)[0]["handover_id"]
    assert client.post(f"/handover/{hid}/ack", data={"actor": sd.OUT}).status_code == 403
    assert client.post(f"/handover/{hid}/ack", data={"actor": sd.IN}).status_code == 200
    page = client.get("/shift/curated").text
    assert page.count(f"Seen and taken over by {sd.IN}") == 2
    assert "state shown: <b>after acknowledgement</b>" in page
