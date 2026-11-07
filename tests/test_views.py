"""Audit-trail line, state chip, model reading card and the closing card, each checked against values from the contract and the database."""
import os
import re

os.environ["CTD_OFFLINE"] = "1"

import pytest
from fastapi.testclient import TestClient

from ctd import contracts, pbip, reader, store
from ctd.app import REQUEST, app
from ctd.catalog import get_catalog

ANS = {"q1": "Which country needs stock this month. Once a month.", "q2": "Stock below four weeks. Then I flag the medicine and country for review.", "q3": "Launch Excellence Lead"}
RECV = "Launch Excellence Lead"


@pytest.fixture()
def client():
    store.reset()
    return TestClient(app, follow_redirects=False)


def trail_lines(html: str) -> list[str]:
    """The contract line(s) as plain text. The glance layout prints it once, in the band; v3 printed it in the strip and on both cards."""
    return [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", "", m)).strip() for m in re.findall(r'<div class="g-meta" data-trail>(.*?)</div>', html, re.S)]


def expected_line(c: dict) -> str:
    cat = get_catalog()
    cnt = contracts.counters()
    month = cat.series(c["scope"]["product_id"], c["scope"]["markets"][0])[-1]["month"]
    return (f"owner {c['owner_id']}, {cat.owner_state(c['owner_id'])} · observed {month} · rule v1 · rendered from contract, no manual edits · "
            f"Assets: {cnt['assets']} · Views: {cnt['views']}")


def test_contract_line_is_printed_once_and_equals_contract_and_database_values(client):
    client.post("/contract/create", data=ANS)
    for _ in range(2):  # after create, then after acknowledge and v2
        c = contracts.latest("CON-SUPPLY-RISK")
        page = client.get("/contract").text
        assert trail_lines(page) == [expected_line(c)]
        main = page.split("<main>")[1]
        assert main.count("rendered from contract") == 1 and main.count("Assets: <b>") == 1  # nowhere else on the page
        hid = "HO-" + client.get("/audit").text.split("HO-")[1][:12]
        client.post(f"/handover/{hid}/ack", data={"actor": RECV})
        client.post("/contract/v2", data={"reason": "add forecast accuracy"})
    page = client.get("/contract").text
    assert "CON-SUPPLY-RISK v2" in page and "Assets: <b>1</b> · Views: <b>2</b>" in page


def test_v3_still_prints_the_trail_on_both_cards(client, monkeypatch):
    client.post("/contract/create", data=ANS)
    monkeypatch.setenv("CTD_VIEW", "v3")
    assert len(re.findall(r'<div class="trail" data-trail>', client.get("/contract").text)) == 2


def test_state_chip_has_shape_and_class_in_both_views(client):
    client.post("/contract/create", data=ANS)
    shapes = {"open": "○", "acknowledged": "◐", "closed": "●"}

    def chips(html):
        return re.findall(r'<span class="chip chip-(\w+)" data-chip="(\w+)"><span class="shape" aria-hidden="true">(.)</span>', html)

    page = client.get("/contract").text
    found = chips(page)
    assert len(found) == 2 and all(cls == st == "open" and sh == shapes["open"] for cls, st, sh in found)
    hid = "HO-" + client.get("/audit").text.split("HO-")[1][:12]
    assert client.post(f"/handover/{hid}/ack", data={"actor": "Supply Analytics Lead"}).status_code == 403
    assert client.post(f"/handover/{hid}/ack", data={"actor": RECV}).status_code == 200
    page = client.get("/contract").text
    found = chips(page)
    assert len(found) == 2 and all(cls == st == "acknowledged" and sh == shapes["acknowledged"] for cls, st, sh in found)
    assert page.count("Seen and taken over by Launch Excellence Lead") == 2


def test_sender_has_no_acknowledge_button_receiver_has_one(client):
    client.post("/contract/create", data=ANS)
    page = client.get("/contract").text
    assert page.count(">Acknowledge<") == 1 and "Try to acknowledge" not in page


def test_old_view_is_one_switch_away(client, monkeypatch):
    client.post("/contract/create", data=ANS)
    monkeypatch.setenv("CTD_VIEW", "v1")
    page = client.get("/contract").text
    assert "Focus rule:" in page and "data-trail" not in page
    monkeypatch.delenv("CTD_VIEW")
    assert "g-meta" in client.get("/contract").text


def test_no_view_text_calls_the_zones_a_company_standard(client):
    client.post("/contract/create", data=ANS)
    page = client.get("/contract").text.lower()
    assert "bayer standard" not in page and "company standard" not in page and "forecast-like" not in page


# ---- model reading ----------------------------------------------------------------------------------
GOOD = {"decision": {"value": "Which country needs stock this month", "fragment": "Which country needs stock this month"},
        "cadence": {"value": "monthly", "fragment": "Once a month"},
        "kpi": {"value": "Inventory Coverage", "fragment": "Stock below four weeks"},
        "threshold": {"value": 4, "fragment": "below four weeks"},
        "direction": {"value": "below", "fragment": "below four weeks"},
        "escalation_to": {"value": RECV, "fragment": RECV}}


def test_offline_card_comes_from_the_fixture_with_cached_badge(client):
    html = client.post("/interview", data=ANS).text
    card = html[html.index('id="reading-card"'):]
    assert 'data-source="cached"' in card and 'badge cached' in card
    assert card.count('class="rfield"') == 6 and card.count('<div class="quote">') == 6
    assert 'id="reading" data-live="0"' in html


def test_wrong_threshold_is_a_named_conflict_and_the_contract_keeps_the_parsed_value(client):
    bad = {**GOOD, "threshold": {"value": 5, "fragment": "below four weeks"}}
    r = reader.build(ANS, bad, "model")
    row = next(x for x in r["rows"] if x["key"] == "threshold")
    assert row["status"] == "conflict" and row["text"] == "model: 5 · parsed: 4 weeks"
    assert [x["key"] for x in r["conflicts"]] == ["threshold"]
    client.post("/contract/create", data=ANS)
    assert contracts.latest("CON-SUPPLY-RISK")["lead_kpi"]["threshold"]["amber"] == 4.0


def test_fragment_not_in_the_answers_is_not_shown_as_a_quote():
    r = reader.build(ANS, {**GOOD, "cadence": {"value": "monthly", "fragment": "every blue moon"}}, "model")
    row = next(x for x in r["rows"] if x["key"] == "cadence")
    assert row["quote"] is None and row["quote_claimed"] and row["status"] == "match"


def test_unknown_kpi_or_role_from_the_model_is_discarded_and_shown():
    r = reader.build(ANS, {**GOOD, "kpi": {"value": "Happiness Index", "fragment": "Stock"}, "escalation_to": {"value": "Wizard", "fragment": RECV}}, "model")
    st = {x["key"]: x for x in r["rows"]}
    assert st["kpi"]["status"] == "rejected" and "discarded" in st["kpi"]["text"] and "Inventory Coverage" in st["kpi"]["text"]
    assert st["escalation_to"]["status"] == "rejected" and "role list" in st["escalation_to"]["text"]


def test_other_answers_get_no_invented_reading(client):
    html = client.post("/interview", data={**ANS, "q1": "Which country needs stock this month. Daily."}).text
    assert "No model reading available" in html


def test_reading_endpoint_degrades_to_the_card_when_offline(client):
    r = client.post("/interview/reading", data=ANS)
    assert r.status_code == 200 and 'data-source="cached"' in r.text


# ---- closing card ------------------------------------------------------------------------------------
def test_closing_card_numbers_equal_the_run(client):
    client.post("/contract/create", data=ANS)
    client.get("/shift")
    client.post("/contract/v2", data={"reason": "add forecast accuracy"})
    page = client.post("/export").text
    cat = get_catalog()
    reqs = len(cat.analyze(REQUEST)["requests"])
    c = contracts.latest("CON-SUPPLY-RISK")
    res = pbip.export(cat, "CON-SUPPLY-RISK")
    again = pbip.export(cat, "CON-SUPPLY-RISK")
    assert res["sha256"] == again["sha256"]
    series_rows = len((pbip.Path(res["root"]) / "data" / "series.csv").read_text().splitlines()) - 1
    closing = page[page.index('id="closing"'):page.index("</aside>")]
    text = re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", closing))
    assert f"{reqs} requests, 0 new dashboards, Assets: 1" in text
    assert f"v1 → v{c['version']}, every change with name, time, reason" in text
    assert "Shift Team Lead got a gap, not a view" in text
    assert f"{len(res['files'])} files, same hash twice" in text
    assert f"series.csv, {series_rows} rows)" in page
    assert "published to a Power BI workspace" in page and "not opened in Power BI Desktop" in page
    assert "Contract.tmdl" in page and "report.json" not in page.split('id="tmdl"')[1]
    assert f"version: {c['version']}" in page and f"owner_id: {c['owner_id']}" in page and "bound to: Series[Pairs below the line]" in page


def test_honest_line_says_so_when_no_refusal_was_shown(client):
    client.post("/contract/create", data=ANS)
    page = client.post("/export").text
    assert "no refusal was shown in this run" in page


# ---- races, hostile model values, one contract per create -------------------------------
def test_parallel_exports_are_serialized_and_the_card_stays_true(client):
    from concurrent.futures import ThreadPoolExecutor
    client.post("/contract/create", data=ANS)
    with ThreadPoolExecutor(10) as ex:
        rs = list(ex.map(lambda _: client.post("/export"), range(10)))
    assert all(r.status_code == 200 for r in rs)
    assert all("same hash twice" in r.text and "hash differs" not in r.text for r in rs)


def test_parallel_first_shift_visits_write_one_refusal_row(client):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(10) as ex:
        list(ex.map(lambda _: client.get("/shift"), range(10)))
    assert len(store.decisions("refusal_seen")) == 1 and store.audit_count("refusal_shown") == 1


@pytest.mark.parametrize("bad", [{"value": {"a": 1}}, {"value": [1]}, {"value": 10 ** 400}, {"value": float("nan")}, {"value": "x" * 5000}, {"value": True}, {"value": None}, "text", 7, None])
def test_hostile_model_values_never_raise(bad):
    for key in ("kpi", "escalation_to", "cadence", "direction", "threshold", "decision"):
        r = reader.build(ANS, {**GOOD, key: bad}, "model")
        row = next(x for x in r["rows"] if x["key"] == key)
        assert row["status"] != "match" and len(row["text"]) < 600


def test_one_letter_decision_is_not_a_match():
    r = reader.build(ANS, {**GOOD, "decision": {"value": "o", "fragment": "o"}}, "model")
    assert next(x for x in r["rows"] if x["key"] == "decision")["status"] == "conflict"


def test_create_twice_keeps_one_version_and_one_handover(client):
    assert client.post("/contract/create", data=ANS).status_code == 303
    assert client.post("/contract/create", data=ANS).status_code == 303
    assert contracts.versions("CON-SUPPLY-RISK") == [1]
    assert client.post("/contract/create", data={**ANS, "q1": "x" * 600}).status_code == 422


@pytest.mark.parametrize("q2", ["Stock below 1e999", "Stock below 4.5.6"])
def test_odd_number_formats_are_refused(client, q2):
    assert client.post("/contract/create", data={**ANS, "q2": q2}).status_code == 422


def test_handover_comes_before_the_pairs_in_the_view(client):
    client.post("/contract/create", data=ANS)
    page = client.get("/contract").text
    first = page.index("<section")
    assert page.index("Hand over", first) < page.index("Next to the lead number", first)


@pytest.mark.parametrize("q2", ["Stock below 4.", "Stock under 3.5.", "Stock below 4 weeks."])
def test_number_before_a_sentence_end_still_parses(client, q2):
    assert client.post("/contract/create", data={**ANS, "q2": q2}).status_code == 303


def test_decision_and_new_version_do_not_overwrite_each_other(client):
    from concurrent.futures import ThreadPoolExecutor
    client.post("/contract/create", data=ANS)
    with ThreadPoolExecutor(2) as ex:
        a = ex.submit(client.post, "/contract/v2", data={"reason": "add forecast accuracy"})
        b = ex.submit(client.post, "/consolidate/CONS-1/decide", data={"choice": "fix_mapping", "reason": "agreed denominator"})
        a.result(), b.result()
    c = contracts.latest("CON-SUPPLY-RISK")
    assert len(c["neighbour_kpis"]) == 3 or contracts.versions("CON-SUPPLY-RISK")[-1] == 2
