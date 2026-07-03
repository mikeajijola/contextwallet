"""Wallet Phase 1 acceptance tests (build brief §2.6).

Same convention as the engine's own Unit 2/3 tests: the embedder is REAL (fastembed), the
LLM adjudicators are the units' shipped offline fakes (`ValueJudge`, `CountingSame`), so the
test is deterministic wherever fastembed's model is reachable.
"""
from __future__ import annotations

import pytest

from contract import Refusal
from db import get_conn
from locators import field_of, source_of
from resolution.capability import mint
from resolution.gate import PredicateGate
from demo import TwinDemo
from wallet.multi_reader import MultiSourceReader
from wallet.registry import SOURCES
from wallet.setup import onboard_all, REQUIRED_FIELDS
from wallet.wallet_projector import WalletProjector, cell_ctx
from wallet.fetch import resolve_value

from tests.test_unit2 import ValueJudge
from tests.test_unit3 import CountingSame

CTX = {"purpose": "wallet_query"}


def _build():
    gate = PredicateGate()
    demo = TwinDemo(get_conn(":memory:"), judge=ValueJudge(), adjudicator=CountingSame(same=True),
                    gate=gate, reader=MultiSourceReader())
    reports = onboard_all(demo)
    return demo, gate, reports


def _served_by_source(store):
    out: dict[str, set[str]] = {}
    for c in store.all_cells():
        out.setdefault(source_of(c.ref.locator), set()).add(field_of(c.ref.locator))
    return out


# --------------------------------------------------------------------------- 1. onboarding
def test_1_all_four_sources_onboard_required_fields_and_region_still_defers():
    demo, gate, reports = _build()
    served = _served_by_source(demo.store)

    for source, fields in REQUIRED_FIELDS.items():
        for f in fields:
            assert f in served.get(source, set()), f"{source}.{f} did not mint a cell"

    # region is still the intentional ontology gap — never silently minted.
    assert "region" not in served.get("crm_b", set())
    assert reports["crm_b"].bands["region"] in ("deferred", "propose_new", "flag", "quarantine")


# --------------------------------------------------------------------------- 2. leak discipline
def test_2_org_projection_has_zero_personal_notes_owner_sees_all():
    demo, gate, _ = _build()
    wproj = WalletProjector(demo.registry, gate, audit=demo.audit, control_plane=demo.control_plane)

    org_cap = mint("acme-assistant", "wallet_query", ["src:crm_a", "src:crm_b", "src:whatsapp_calls"])
    owner_cap = mint("colin", "wallet_query")

    all_cells = demo.store.all_cells()
    org_proj = wproj.project(all_cells, org_cap, CTX)
    owner_proj = wproj.project(all_cells, owner_cap, CTX)

    org_sources = {source_of(pc.ref.locator) for pc in org_proj.cells.values()}
    owner_sources = {source_of(pc.ref.locator) for pc in owner_proj.cells.values()}

    assert "personal_notes" not in org_sources, "org must see ZERO personal_notes cells"
    assert "personal_notes" in owner_sources
    assert {"crm_a", "crm_b", "whatsapp_calls"} <= org_sources


# --------------------------------------------------------------------------- 3. transcript gate
def test_3_org_derefs_topic_but_refused_on_transcript_owner_derefs_both():
    demo, gate, _ = _build()
    wproj = WalletProjector(demo.registry, gate, audit=demo.audit, control_plane=demo.control_plane)

    org_cap = mint("acme-assistant", "wallet_query", ["src:crm_a", "src:crm_b", "src:whatsapp_calls"])
    owner_cap = mint("colin", "wallet_query")

    all_cells = demo.store.all_cells()
    org_proj = wproj.project(all_cells, org_cap, CTX)
    owner_proj = wproj.project(all_cells, owner_cap, CTX)

    topic_cell = next(c for c in all_cells
                      if source_of(c.ref.locator) == "whatsapp_calls" and field_of(c.ref.locator) == "topic")
    transcript_cell = next(c for c in all_cells
                           if source_of(c.ref.locator) == "whatsapp_calls" and field_of(c.ref.locator) == "transcript_ref")

    org_topic = resolve_value(demo.ladder, org_proj.cells[topic_cell.cell_id], org_cap, CTX)
    assert not isinstance(org_topic, Refusal)
    assert "pricing" in org_topic.lower()

    org_transcript = resolve_value(demo.ladder, org_proj.cells[transcript_cell.cell_id], org_cap, CTX)
    assert isinstance(org_transcript, Refusal)
    assert org_transcript.message == "not available to you"

    owner_topic = resolve_value(demo.ladder, owner_proj.cells[topic_cell.cell_id], owner_cap, CTX)
    owner_transcript = resolve_value(demo.ladder, owner_proj.cells[transcript_cell.cell_id], owner_cap, CTX)
    assert not isinstance(owner_topic, Refusal)
    assert not isinstance(owner_transcript, Refusal)
    assert owner_transcript == "wa_store://call_c1"


# --------------------------------------------------------------------------- 4. revocation
def test_4_revoke_empties_projection_and_closes_a_cached_value():
    demo, gate, _ = _build()
    wproj = WalletProjector(demo.registry, gate, audit=demo.audit, control_plane=demo.control_plane)
    org_cap = mint("acme-assistant", "wallet_query", ["src:crm_a", "src:crm_b", "src:whatsapp_calls"])

    all_cells = demo.store.all_cells()
    org_proj = wproj.project(all_cells, org_cap, CTX)
    assert len(org_proj.cells) > 0

    topic_cell = next(c for c in all_cells
                      if source_of(c.ref.locator) == "whatsapp_calls" and field_of(c.ref.locator) == "topic")
    first = resolve_value(demo.ladder, org_proj.cells[topic_cell.cell_id], org_cap, CTX)
    assert not isinstance(first, Refusal)   # materialised + cached

    gate.revoke(org_cap.id())

    org_proj2 = wproj.project(all_cells, org_cap, CTX)
    assert len(org_proj2.cells) == 0, "revoked cap must see an EMPTY projection"

    # the cached value re-authorises on every read: a revoked cap gets Refusal even though
    # the ladder's materialised cache still holds a fresh entry for this cell.
    stale_pcell = org_proj.cells[topic_cell.cell_id]
    second = resolve_value(demo.ladder, stale_pcell, org_cap, CTX)
    assert isinstance(second, Refusal)


# --------------------------------------------------------------------------- 5. sqlite path
def test_5_multi_reader_reads_seeded_literals_for_every_source():
    reader = MultiSourceReader()
    from contract import Reference
    from locators import make_locator

    checks = [
        ("crm_a", "a1", "full_name", "Colin Marsh"),
        ("crm_b", "b1", "name", "C. Marsh"),
        ("whatsapp_calls", "c1", "transcript_ref", "wa_store://call_c1"),
        ("personal_notes", "n2", "topic", "mortgage renewal"),
    ]
    for source, row_key, field, expected in checks:
        ref = Reference(source=source, locator=make_locator(source, row_key, field), resolver=source)
        assert reader.read_value(ref) == expected

    # the two personal sources have NO csv twin — this is the sqlite path end to end.
    assert SOURCES["personal_notes"]["fmt"] == "sqlite"
    assert SOURCES["whatsapp_calls"]["fmt"] == "sqlite"
