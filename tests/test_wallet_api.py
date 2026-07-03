"""Wallet Phase 2 acceptance tests (build brief §3 "Phase 2 acceptance").

Same convention as the rest of the suite: real embedder, offline judge/adjudicator fakes
(`wallet.adjudicators.ValueJudge`/`CountingSame`, wired inside `wallet.api.WalletState`).
Each test gets an isolated in-memory wallet via `reset_state(":memory:")`.
"""
from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from wallet.api import app, reset_state

ALL_SOURCES = ["crm_a", "crm_b", "whatsapp_calls", "personal_notes"]


@pytest.fixture()
def client():
    with TestClient(app) as c:
        reset_state(":memory:")   # isolated per test, overriding whatever lifespan built
        yield c


def _connect_all(client):
    for s in ALL_SOURCES:
        r = client.post(f"/connectors/{s}/connect")
        assert r.status_code == 200, r.text


def _cap_id(client, consumer_id: str) -> str:
    consumers = client.get("/consumers").json()
    return next(c for c in consumers if c["consumer_id"] == consumer_id)["cap_id"]


def test_connect_all_four_sources(client):
    _connect_all(client)
    rows = client.get("/connectors").json()
    assert {r["source"]: r["status"] for r in rows} == {s: "connected" for s in ALL_SOURCES}


def test_owner_deal_status_signal_and_role_conflict(client):
    _connect_all(client)
    colin_cap = _cap_id(client, "colin")

    r = client.post("/ask", json={"cap_id": colin_cap, "question_id": "deal_status"})
    assert r.status_code == 200
    body = r.json()
    kinds = {c["kind"] for c in body["cards"]}
    assert "signal" in kinds
    role_cards = [c for c in body["cards"] if c["kind"] == "conflict_ordered"
                 and c.get("ontology_node") == "role"]
    assert role_cards, f"expected a role conflict_ordered card, got {body['cards']}"
    role = role_cards[0]
    values = {v["value"] for v in role["values"]}
    assert values == {"VP Engineering", "Director, Platform"}
    assert role["values"][role["default_selection"]]["value"] == "VP Engineering"   # most-recent


def test_acme_deal_status_has_no_personal_notes_and_transcript_is_refused(client):
    _connect_all(client)
    acme_cap = _cap_id(client, "acme")
    colin_cap = _cap_id(client, "colin")

    r = client.post("/ask", json={"cap_id": acme_cap, "question_id": "deal_status"})
    assert r.status_code == 200
    dump = str(r.json())
    assert "personal_notes" not in dump
    assert "mortgage" not in dump.lower()
    assert "hinted" not in dump.lower()

    refused = client.post("/ask", json={"cap_id": acme_cap, "question_id": "open_transcript"}).json()
    assert refused["answer_kind"] == "refusal"
    assert refused["cards"][0]["message"] == "not available to you"

    owner = client.post("/ask", json={"cap_id": colin_cap, "question_id": "open_transcript"}).json()
    assert owner["answer_kind"] == "agreed"
    assert "Femi" in owner["cards"][0]["value"]


def test_partner_sees_only_the_two_shared_rows(client):
    _connect_all(client)
    r = client.patch("/consumers/partner", json={"active": True})
    assert r.status_code == 200
    partner_cap = r.json()["cap_id"]

    g = client.get("/graph", params={"cap_id": partner_cap}).json()
    cell_node_ids = {n["id"].removeprefix("cell:") for n in g["nodes"] if n["kind"] == "cell"}
    source_nodes = {n["label"] for n in g["nodes"] if n["kind"] == "source"}
    assert source_nodes == {"CRM A", "CRM B"}
    assert len(cell_node_ids) > 0

    # every visible cell must belong to row a1 (crm_a) or b1 (crm_b) — never any other row.
    import wallet.api as wallet_api
    from locators import row_key_of
    allowed_rows = {"a1", "b1"}
    for cell in wallet_api.state.demo.store.all_cells():
        if cell.cell_id in cell_node_ids:
            assert row_key_of(cell.ref.locator) in allowed_rows


def test_acme_deactivate_empties_graph_and_refuses_ask(client):
    _connect_all(client)
    acme_cap = _cap_id(client, "acme")

    r = client.patch("/consumers/acme", json={"active": False})
    assert r.status_code == 200
    assert r.json()["cap_id"] is None

    stale = client.get("/graph", params={"cap_id": acme_cap})
    assert stale.status_code == 404

    ask_stale = client.post("/ask", json={"cap_id": acme_cap, "question_id": "deal_status"})
    assert ask_stale.status_code == 404
