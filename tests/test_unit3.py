"""Unit 3 acceptance tests — gate, projection, query-time resolution.

Real fastembed (test 5 needs a genuine middle-band cosine for Dana). The LLM adjudicator is
always a counting fake, so tests stay offline and deterministic.
"""
from __future__ import annotations
import csv
from pathlib import Path

import pytest

from contract import Cell, Reference, TypeDescriptor, Refusal, ProjectedCell
from db import get_conn
from locators import make_locator, parse_locator, row_key_of
from twin_core.store import SqliteMasterTableStore
from twin_core.inversion import build_cells, seed_grouping
from resolution.gate import (
    PredicateGate, PolicyRegistry, OPEN, SECRET, ROLE_GATED, HR_SCOPED, hr_dereference, allow,
)
from resolution.projection import Projector
from resolution.resolver import Resolver, GroupingOverlay
from resolution.capability import mint, demo_analyst_cap, demo_restricted_cap
from contract import CellPolicy

ROOT = Path(__file__).resolve().parent.parent
SEED = ROOT / "seed"


# --------------------------------------------------------------------------- fakes
class FakeReader:
    """read_value(ref) -> the seed CSV value at (source, row_key, field)."""

    def __init__(self):
        self.rows = {}
        for src, path, keycol in [("crm_a", SEED / "crm_a.csv", "id"),
                                  ("crm_b", SEED / "crm_b.csv", "contact_id")]:
            for r in csv.DictReader(open(path)):
                self.rows[(src, r[keycol])] = r
        self.read_count = 0

    def read_value(self, ref: Reference):
        self.read_count += 1
        src, rk, field = parse_locator(ref.locator)
        return self.rows[(src, rk)].get(field, "")


class SpyGate(PredicateGate):
    def __init__(self):
        self.calls = []

    def check(self, cap, predicate, ctx):
        result = super().check(cap, predicate, ctx)
        self.calls.append((predicate, result))
        return result


class FakeAudit:
    def __init__(self):
        self.entries = []

    def append(self, e):
        self.entries.append(e)


class FakeControlPlane:
    def __init__(self, version=7):
        self._v = version

    def current_version(self):
        return self._v


class CountingSame:
    def __init__(self, same=True):
        self.calls = 0
        self.same = same

    def __call__(self, a, b):
        self.calls += 1
        return {"same": self.same, "reason": "fake"}


class BrokenSame:
    def __call__(self, a, b):
        raise RuntimeError("LLM down")


class NoWriteStore:
    """Wraps the real store for reads; explodes on any durable grouping write."""

    def __init__(self, store):
        self.store = store
        self.set_grouping_calls = 0

    def all_cells(self):
        return self.store.all_cells()

    def cells_for(self, pid):
        return self.store.cells_for(pid)

    def set_grouping(self, *a, **k):
        self.set_grouping_calls += 1
        raise AssertionError("resolver must never call set_grouping (overlay-only)")

    def put_cell(self, *a, **k):
        raise AssertionError("resolver must never write cells")

    def put_cell_for(self, *a, **k):
        raise AssertionError("resolver must never write cells")


# --------------------------------------------------------------------------- helpers
_FIELDS = {
    "crm_a": [("full_name", "person"), ("email", "email"), ("company", "organisation"), ("title", "role")],
    "crm_b": [("name", "person"), ("primary_email", "email"), ("org_name", "organisation"), ("job_role", "role")],
}


class _DummyReader:
    def read_value(self, ref):
        raise AssertionError("build_cells must never read")


def seed_store(conn) -> SqliteMasterTableStore:
    """Mint Colin (a1/b1) and Dana (a2/b2) identifying + role cells with the durable seed grouping."""
    store = SqliteMasterTableStore(conn)
    groups = seed_grouping([
        ("crm_a", "a1", "colin.marsh@stripe.com"),
        ("crm_b", "b1", "colin.marsh@stripe.com"),
        ("crm_a", "a2", "dana.osei@acme.io"),
        ("crm_b", "b2", ""),  # blank -> ungrouped (the Dana gap)
    ])
    for src, rk in [("crm_a", "a1"), ("crm_b", "b1"), ("crm_a", "a2"), ("crm_b", "b2")]:
        tds = [(f, TypeDescriptor(kind="string", shape=None, ontology_node=n)) for f, n in _FIELDS[src]]
        for c in build_cells(src, rk, tds, _DummyReader(), "role_gated"):
            store.put_cell_for(groups.get((src, rk)), c)
    return store


def _row_ref(src, rk):
    return Reference(source=src, locator=make_locator(src, rk, "__row__"), resolver=src)


def _cell(policy_id, node="role", src="crm_a", rk="a1", field="title"):
    return Cell(
        cell_id=f"{policy_id}-{src}-{rk}-{field}",
        ref=Reference(source=src, locator=make_locator(src, rk, field), resolver=src),
        type=TypeDescriptor(kind="string", shape=None, ontology_node=node),
        policy_id=policy_id, state="placeholder", materialised=None,
    )


# --------------------------------------------------------------------------- tests
def test_1_two_viewers_two_maps():
    reg, gate = PolicyRegistry(), PredicateGate()
    proj = Projector(reg, gate, audit=FakeAudit(), control_plane=FakeControlPlane())
    cells = [_cell("open", field="title"), _cell("hr_scoped", field="salary")]

    analyst = proj.project(cells, demo_analyst_cap(), {"purpose": "cross_source_query"})
    restricted = proj.project(cells, demo_restricted_cap(), {"purpose": "cross_source_query"})

    assert set(analyst.cells) != set(restricted.cells)          # different MAPS
    assert "hr_scoped-crm_a-a1-salary" in analyst.cells         # HR sees the hr_scoped cell
    assert "hr_scoped-crm_a-a1-salary" not in restricted.cells  # restricted does not
    assert "open-crm_a-a1-title" in restricted.cells            # both see the open cell


def test_2_secret_cell_is_absent_not_masked():
    reg, gate = PolicyRegistry(), PredicateGate()
    proj = Projector(reg, gate)
    cells = [_cell("open", field="title"), _cell("secret", field="ssn")]

    table = proj.project(cells, demo_analyst_cap(), {"purpose": "cross_source_query"})

    assert "secret-crm_a-a1-ssn" not in table.cells   # ABSENT, not a None placeholder
    assert len(table.cells) == 1
    assert all("secret" not in cid for cid in table.cells)


def test_3_projection_carries_dereference_unevaluated_and_no_value():
    # a role_gated cell: projection shows type+ref, carries dereference UNEVALUATED, no value
    spy_calls = {"n": 0}

    def spy_deref(cap, ctx):
        spy_calls["n"] += 1
        return True

    policy = CellPolicy(policy_id="deref_spy", see_existence=allow, see_type=allow,
                        see_state=allow, dereference=spy_deref)
    reg = PolicyRegistry([policy])
    proj = Projector(reg, PredicateGate())
    cell = _cell("deref_spy", field="title")

    table = proj.project([cell], demo_restricted_cap(), {"purpose": "cross_source_query"})
    pc = table.cells[cell.cell_id]

    assert pc.type is not None and pc.ref is not None      # map facets shown
    assert callable(pc.dereference)                        # dereference carried
    assert spy_calls["n"] == 0                             # ...but NEVER evaluated (hole 6)
    assert not hasattr(pc, "value")                        # projection returns no value
    assert "value" not in pc.model_dump()


def test_4_resolve_colin_auto_via_shared_email():
    conn = get_conn(":memory:")
    store = seed_store(conn)
    gate, reader, adj = SpyGate(), FakeReader(), CountingSame()
    resolver = Resolver(gate, reader, store, adjudicator=adj, audit=FakeAudit())

    overlay = GroupingOverlay()
    result = resolver.resolve([_row_ref("crm_a", "a1"), _row_ref("crm_b", "b1")],
                              demo_analyst_cap(), {"purpose": "cross_source_query"}, overlay)

    assert not isinstance(result, Refusal)
    assert len(result.member_refs) == 2                    # Colin resolved across both CRMs
    assert any(res for _, res in gate.calls)               # gate.check was invoked (allowed)
    assert adj.calls == 0                                  # shared email -> AUTO, no LLM


def test_5_resolve_dana_middle_band_llm_and_memoised():
    conn = get_conn(":memory:")
    store = seed_store(conn)
    gate, reader, adj = SpyGate(), FakeReader(), CountingSame(same=True)
    resolver = Resolver(gate, reader, store, adjudicator=adj)

    overlay = GroupingOverlay()
    ctx = {"purpose": "cross_source_query"}
    refs = [_row_ref("crm_a", "a2"), _row_ref("crm_b", "b2")]

    r1 = resolver.resolve(refs, demo_analyst_cap(), ctx, overlay)
    r2 = resolver.resolve(refs, demo_analyst_cap(), ctx, overlay)  # re-run

    assert len(r1.member_refs) == 2                        # Dana merged on name+org
    assert adj.calls == 1                                  # ONE LLM call, memoised on re-run
    assert r1.member_refs and r2.member_refs

    # the Dana rule: overlay.cells_for unions store seed + live merge -> both CRMs' role cells
    dana_roles = overlay.cells_for(store, r1.principal_id, node="role")
    assert {row_key_of(c.ref.locator) for c in dana_roles} == {"a2", "b2"}


def test_6_persists_nothing_and_works_from_nothing():
    conn = get_conn(":memory:")
    store = seed_store(conn)

    def snapshot():
        return {r["cell_id"]: r["principal_id"]
                for r in conn.execute("SELECT cell_id, principal_id FROM cells").fetchall()}

    before = snapshot()
    resolver = Resolver(PredicateGate(), FakeReader(), store, adjudicator=CountingSame())
    resolver.resolve([_row_ref("crm_a", "a2"), _row_ref("crm_b", "b2")],
                     demo_analyst_cap(), {"purpose": "cross_source_query"}, GroupingOverlay())

    # cells.principal_id column is UNCHANGED (overlay wrote nothing durable)
    assert snapshot() == before
    # no same-as / resolution / grouping table was created
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert not any(k in t.lower() for t in tables for k in ("same", "resolution", "grouping"))

    # a fresh resolver with a BROKEN llm still works from nothing (Colin needs no LLM)
    r = Resolver(PredicateGate(), FakeReader(), store, adjudicator=BrokenSame()).resolve(
        [_row_ref("crm_a", "a1"), _row_ref("crm_b", "b1")],
        demo_analyst_cap(), {"purpose": "cross_source_query"}, GroupingOverlay())
    assert not isinstance(r, Refusal) and len(r.member_refs) == 2


def test_7_overlay_only_no_durable_write_path():
    conn = get_conn(":memory:")
    store = seed_store(conn)
    guard = NoWriteStore(store)
    resolver = Resolver(PredicateGate(), FakeReader(), guard, adjudicator=CountingSame())

    overlay = GroupingOverlay()
    resolver.resolve([_row_ref("crm_a", "a2"), _row_ref("crm_b", "b2")],
                     demo_analyst_cap(), {"purpose": "cross_source_query"}, overlay)

    assert guard.set_grouping_calls == 0                   # never written durably
    assert not hasattr(Resolver, "set_grouping")           # no durable write path exists
    # Dana's merge lives ONLY in this overlay...
    assert overlay.principal_of("a2") == overlay.principal_of("b2") is not None
    # ...and is gone once the overlay is dropped (a fresh one knows nothing)
    assert GroupingOverlay().principal_of("a2") is None


def test_8_refusal_is_flat():
    conn = get_conn(":memory:")
    store = seed_store(conn)
    resolver = Resolver(PredicateGate(), FakeReader(), store, adjudicator=CountingSame())

    # restricted cap: no clearance:hr -> gate denies the identifying read
    result = resolver.resolve([_row_ref("crm_a", "a1"), _row_ref("crm_b", "b1")],
                              demo_restricted_cap(), {"purpose": "cross_source_query"}, GroupingOverlay())

    assert isinstance(result, Refusal)
    assert result.message == "not available to you"        # flat, carries no cell-derived field
    assert set(result.model_dump().keys()) == {"message"}
