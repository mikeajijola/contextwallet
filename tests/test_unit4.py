"""Unit 4 acceptance tests — SourceReader, fetch ladder (Gate 2), conflict, overlay join.

Consumed Unit 3 interfaces (Gate, PolicyRegistry, GroupingOverlay) are stubbed here; the real
ones drop in at integration via the frozen signatures. Uses the real seed CSVs (no network).
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone

import pytest

from contract import (
    Capability, Reference, TypeDescriptor, ProjectedCell, Refusal, Symbol,
)
from db import get_conn
from locators import make_locator, row_key_of
from twin_core.store import SqliteMasterTableStore
from twin_core.inversion import build_cells, seed_grouping
from fetch.source_reader import CsvSourceReader
from fetch.adapter import CsvAdapter
from fetch.cache import MaterialisedCache
from fetch.ladder import DefaultFetchLadder
from fetch.join import cross_source_query


# --------------------------------------------------------------------------- stubs
def _mint(holder, purpose, caveats, ttl_minutes=60):
    return Capability(holder=holder, purpose=purpose, caveats=list(caveats),
                      expiry=datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes))


def analyst():
    return _mint("analyst", "cross_source_query", ["clearance:hr"])


def restricted():
    return _mint("contractor", "cross_source_query", [])


def hr(cap, ctx):
    return cap.purpose in {"cross_source_query", "dsar_response"} and "clearance:hr" in cap.caveats


def allow(cap, ctx):
    return True


class StubGate:
    def check(self, cap, predicate, ctx):
        expiry = cap.expiry
        if expiry.tzinfo is None:
            expiry = expiry.replace(tzinfo=timezone.utc)
        if expiry < datetime.now(timezone.utc):
            return False
        return bool(predicate(cap, ctx))


class _Pol:
    def __init__(self, deref):
        self.dereference = deref


class StubRegistry:
    def __init__(self):
        self._p = {"role_gated": _Pol(hr), "open": _Pol(allow)}

    def get(self, policy_id):
        return self._p[policy_id]


class StubOverlay:
    """Minimal query-scoped overlay: merge + union cells_for (store durable + live merges)."""

    def __init__(self):
        self._of = {}
        self._n = 0

    def merge(self, row_keys):
        pid = f"ovl_{self._n + 1}"
        self._n += 1
        for rk in row_keys:
            self._of[rk] = pid
        return pid

    def cells_for(self, store, principal_id, node=None):
        out = {c.cell_id: c for c in store.cells_for(principal_id)}
        rows = {rk for rk, p in self._of.items() if p == principal_id}
        if rows:
            for c in store.all_cells():
                if row_key_of(c.ref.locator) in rows:
                    out[c.cell_id] = c
        cells = list(out.values())
        return [c for c in cells if node is None or c.type.ontology_node == node]


# --------------------------------------------------------------------------- fixtures
_FIELDS = {
    "crm_a": [("full_name", "person"), ("email", "email"), ("company", "organisation"), ("title", "role")],
    "crm_b": [("name", "person"), ("primary_email", "email"), ("org_name", "organisation"), ("job_role", "role")],
}


class _DummyReader:
    def read_value(self, ref):
        raise AssertionError("build_cells must never read")


def seed_store(conn):
    store = SqliteMasterTableStore(conn)
    groups = seed_grouping([
        ("crm_a", "a1", "colin.marsh@stripe.com"), ("crm_b", "b1", "colin.marsh@stripe.com"),
        ("crm_a", "a2", "dana.osei@acme.io"), ("crm_b", "b2", ""),
    ])
    for src, rk in [("crm_a", "a1"), ("crm_b", "b1"), ("crm_a", "a2"), ("crm_b", "b2")]:
        tds = [(f, TypeDescriptor(kind="string", shape=None, ontology_node=n)) for f, n in _FIELDS[src]]
        for c in build_cells(src, rk, tds, _DummyReader(), "role_gated"):
            store.put_cell_for(groups.get((src, rk)), c)
    return store, groups


def _role_pcell(src, rk, field):
    return ProjectedCell(
        cell_id=f"{src}-{rk}-{field}",
        ref=Reference(source=src, locator=make_locator(src, rk, field), resolver=src),
        type=TypeDescriptor(kind="string", shape=None, ontology_node="role"),
        state="placeholder", dereference=hr,
    )


def _ladder():
    reader = CsvSourceReader()
    adapter = CsvAdapter(reader)
    cache = MaterialisedCache(get_conn(":memory:"))
    return DefaultFetchLadder(StubGate(), adapter, cache), adapter, reader


# --------------------------------------------------------------------------- tests
def test_1_source_reader_reads_seed():
    reader = CsvSourceReader()
    ref = Reference(source="crm_a", locator=make_locator("crm_a", "a1", "title"), resolver="crm_a")
    assert reader.read_value(ref) == "VP Engineering"
    assert "region" in reader.list_fields("crm_b")           # divergent schema
    assert reader.sample_field("crm_a", "email", 3) == [
        "colin.marsh@stripe.com", "dana.osei@acme.io", "priya.nair@stripe.com"]


def test_2_fetch_gate_value_vs_refusal():
    ladder, _, _ = _ladder()
    pcell = _role_pcell("crm_a", "a1", "title")
    ctx = {"purpose": "cross_source_query"}

    assert ladder.resolve_value(pcell, analyst(), ctx) == "VP Engineering"     # HR -> value
    assert isinstance(ladder.resolve_value(pcell, restricted(), ctx), Refusal)  # no HR -> flat refusal


def test_3_materialised_cache_prevents_refetch():
    ladder, adapter, _ = _ladder()
    pcell = _role_pcell("crm_a", "a1", "title")
    ctx = {"purpose": "cross_source_query"}

    v1 = ladder.resolve_value(pcell, analyst(), ctx)
    v2 = ladder.resolve_value(pcell, analyst(), ctx)   # served from cache
    assert v1 == v2 == "VP Engineering"
    assert adapter.fetch_count == 1                     # read once, cached thereafter


def test_4_symbolic_tier_returns_token_no_read():
    ladder, adapter, _ = _ladder()
    pcell = _role_pcell("crm_a", "a1", "title")

    result = ladder.resolve_value(pcell, analyst(), {"symbolic": True})
    assert isinstance(result, Symbol)
    assert adapter.fetch_count == 0                     # no value ever read


def test_5_conflict_ordered_colin():
    conn = get_conn(":memory:")
    store, groups = seed_store(conn)
    ladder, _, reader = _ladder()
    colin_pid = groups[("crm_a", "a1")]

    cs = cross_source_query(store, StubOverlay(), colin_pid, "role",
                            analyst(), {"purpose": "cross_source_query"},
                            ladder, StubRegistry(), reader)

    assert cs.status == "conflict_ordered"
    assert len(cs.values) == 2
    assert cs.values[cs.default_selection].value == "VP Engineering"   # latest wins the default
    assert {v.source for v in cs.values} == {"crm_a", "crm_b"}


def test_6_conflict_unordered_dana_via_overlay():
    conn = get_conn(":memory:")
    store, _ = seed_store(conn)
    ladder, _, reader = _ladder()

    overlay = StubOverlay()
    dana_pid = overlay.merge(["a2", "b2"])             # the live resolution (no shared email)

    cs = cross_source_query(store, overlay, dana_pid, "role",
                            analyst(), {"purpose": "cross_source_query"},
                            ladder, StubRegistry(), reader)

    assert cs.status == "conflict_unordered"           # b2 has no timestamp
    assert cs.default_selection is None                # nothing silently chosen
    assert {v.source for v in cs.values} == {"crm_a", "crm_b"}
    assert {v.value for v in cs.values} == {"Head of Ops", "Operations Lead"}


def test_7_dana_rule_cold_store_misses_her():
    conn = get_conn(":memory:")
    store, _ = seed_store(conn)
    overlay = StubOverlay()
    dana_pid = overlay.merge(["a2", "b2"])

    # cold store has no such principal — Dana is invisible without the overlay
    assert store.cells_for(dana_pid) == []
    # the overlay unions her across both CRMs
    via_overlay = overlay.cells_for(store, dana_pid, "role")
    assert {row_key_of(c.ref.locator) for c in via_overlay} == {"a2", "b2"}


def test_8_cached_value_reauthorises_every_read():
    ladder, adapter, _ = _ladder()
    pcell = _role_pcell("crm_a", "a1", "title")
    ctx = {"purpose": "cross_source_query"}

    assert ladder.resolve_value(pcell, analyst(), ctx) == "VP Engineering"   # cache populated
    # a different, unauthorised cap does NOT get the cached value
    assert isinstance(ladder.resolve_value(pcell, restricted(), ctx), Refusal)
    # an EXPIRED (revoked) capability is closed even though the value is cached
    expired = _mint("analyst", "cross_source_query", ["clearance:hr"], ttl_minutes=-1)
    assert isinstance(ladder.resolve_value(pcell, expired, ctx), Refusal)
    assert adapter.fetch_count == 1                     # still only the one authorised read
