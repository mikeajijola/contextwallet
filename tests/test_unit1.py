"""Unit 1 acceptance tests — the map data layer.

Covers: cell round-trip (incl. materialised provenance), fail-closed invariants,
reference-inversion never fetching, the conflict multiplicity, cell_id stability, and
the overridable grouping model (seed grouping + live re-group + no-clobber re-put).
"""
from __future__ import annotations
from datetime import datetime, timedelta

import pytest
from pydantic import ValidationError

from contract import (
    Cell,
    MaterialisedValue,
    Reference,
    TypeDescriptor,
    Value,
)
from db import get_conn
from locators import make_locator
from twin_core.inversion import build_cells, seed_grouping
from twin_core.store import SqliteMasterTableStore


# --------------------------------------------------------------------------- helpers
class SpyReader:
    """A `SourceReader` whose `read_value` MUST NOT be called during map-build."""

    def __init__(self) -> None:
        self.read_calls = 0

    def list_fields(self, source: str) -> list[str]:
        return ["full_name", "email", "title", "company"]

    def sample_field(self, source: str, field: str, n: int = 3) -> list[str]:
        return ["sample"] * n

    def read_value(self, ref: Reference) -> Value:
        self.read_calls += 1
        raise AssertionError("read_value must never be called at map-build time")


def _store() -> SqliteMasterTableStore:
    # An isolated in-memory db per test; the store shares this one connection.
    return SqliteMasterTableStore(get_conn(":memory:"))


def _role() -> TypeDescriptor:
    return TypeDescriptor(kind="string", shape=None, ontology_node="role")


def _cell(source: str, row_key: str, field: str, node: str) -> Cell:
    loc = make_locator(source, row_key, field)
    import hashlib
    return Cell(
        cell_id=hashlib.sha256(loc.encode()).hexdigest()[:16],
        ref=Reference(source=source, locator=loc, resolver=source),
        type=TypeDescriptor(kind="string", shape=None, ontology_node=node),
        policy_id="default",
        state="placeholder",
        materialised=None,
    )


# --------------------------------------------------------------------------- tests
def test_1_roundtrip_placeholder_and_materialised():
    store = _store()
    placeholder = _cell("crm_a", "a1", "email", "email")

    loc = make_locator("crm_a", "a1", "title")
    import hashlib
    materialised = Cell(
        cell_id=hashlib.sha256(loc.encode()).hexdigest()[:16],
        ref=Reference(source="crm_a", locator=loc, resolver="crm_a"),
        type=_role(),
        policy_id="default",
        state="materialised",
        materialised=MaterialisedValue(
            value="VP Engineering",
            fetched_under="cap-abc123",
            fetched_at=datetime(2025, 11, 2, 12, 0, 0),
            ttl=timedelta(hours=1),
            origin_policy_id="default",
        ),
    )

    store.put_cell_for("colin", placeholder)
    store.put_cell_for("colin", materialised)

    cells = {c.cell_id: c for c in store.cells_for("colin")}
    assert len(cells) == 2

    got = cells[materialised.cell_id]
    assert got.state == "materialised"
    assert got.materialised is not None
    # provenance survives the round-trip
    assert got.materialised.fetched_under == "cap-abc123"
    assert got.materialised.origin_policy_id == "default"
    assert got.materialised.value == "VP Engineering"
    assert got.materialised.ttl == timedelta(hours=1)

    # the placeholder stays a placeholder with no value
    assert cells[placeholder.cell_id].state == "placeholder"
    assert cells[placeholder.cell_id].materialised is None


def test_2_fail_closed_invariants():
    # state=materialised with materialised=None -> rejected at construction
    with pytest.raises(ValidationError):
        Cell(
            cell_id="bad-1",
            ref=Reference(source="crm_a", locator=make_locator("crm_a", "x", "email"), resolver="crm_a"),
            type=_role(),
            policy_id="default",
            state="materialised",
            materialised=None,
        )

    # empty ontology_node -> rejected (fail-closed)
    with pytest.raises(ValidationError):
        Cell(
            cell_id="bad-2",
            ref=Reference(source="crm_a", locator=make_locator("crm_a", "x", "email"), resolver="crm_a"),
            type=TypeDescriptor(kind="string", shape=None, ontology_node=""),
            policy_id="default",
            state="placeholder",
            materialised=None,
        )


def test_3_build_cells_never_fetches():
    spy = SpyReader()
    fields = [
        ("full_name", TypeDescriptor(kind="string", shape=None, ontology_node="person")),
        ("email", TypeDescriptor(kind="string", shape=None, ontology_node="email")),
        ("title", _role()),
    ]
    cells = build_cells("crm_a", "a1", fields, reader=spy, default_policy_id="default")

    assert len(cells) == 3
    assert spy.read_calls == 0  # values never touched at map-build time
    assert all(c.state == "placeholder" for c in cells)
    assert all(c.materialised is None for c in cells)
    # refs point back at the source via an opaque locator; no identity, no values
    email_cell = next(c for c in cells if c.type.ontology_node == "email")
    assert email_cell.ref.locator == make_locator("crm_a", "a1", "email")
    assert email_cell.ref.resolver == "crm_a"


def test_4_conflict_multiplicity_for_role():
    store = _store()

    # Colin across both CRMs -> two role cells, grouped to one principal at write time.
    for source, row_key, field in [("crm_a", "a1", "title"), ("crm_b", "b1", "job_role")]:
        for c in build_cells(source, row_key, [(field, _role())], reader=SpyReader(), default_policy_id="default"):
            store.put_cell_for("colin", c)

    # Dana too, so we prove filtering by principal works.
    for source, row_key, field in [("crm_a", "a2", "title"), ("crm_b", "b2", "job_role")]:
        for c in build_cells(source, row_key, [(field, _role())], reader=SpyReader(), default_policy_id="default"):
            store.put_cell_for("dana", c)

    colin_roles = store.cells_for_node("colin", "role")
    assert len(colin_roles) == 2
    assert {c.ref.source for c in colin_roles} == {"crm_a", "crm_b"}

    # a different node returns nothing for Colin
    assert store.cells_for_node("colin", "email") == []
    # Dana is isolated
    assert len(store.cells_for_node("dana", "role")) == 2


def test_5_cell_id_stability_idempotent():
    fields = [("title", _role())]
    first = build_cells("crm_a", "a1", fields, reader=SpyReader(), default_policy_id="default")
    second = build_cells("crm_a", "a1", fields, reader=SpyReader(), default_policy_id="default")

    assert first[0].cell_id == second[0].cell_id

    # re-onboarding upserts rather than duplicating
    store = _store()
    store.put_cell_for("colin", first[0])
    store.put_cell_for("colin", second[0])
    assert len(store.all_cells()) == 1


def test_6_seed_grouping_merges_colin_not_dana():
    # exact-email seed: Colin shares an email across CRMs -> merged; Dana's B record is
    # blank -> she is NOT merged by the seed (left for query-time resolution).
    records = [
        ("crm_a", "a1", "colin.marsh@stripe.com"),
        ("crm_b", "b1", "COLIN.MARSH@stripe.com"),   # case-insensitive match
        ("crm_a", "a2", "dana.osei@acme.io"),
        ("crm_b", "b2", ""),                          # blank -> ungrouped
    ]
    groups = seed_grouping(records)

    assert groups[("crm_a", "a1")] == groups[("crm_b", "b1")]   # Colin merged
    assert ("crm_b", "b2") not in groups                        # Dana B ungrouped
    assert groups[("crm_a", "a2")] != groups[("crm_a", "a1")]   # Dana A its own group


def test_7_live_regroup_and_no_clobber_reput():
    store = _store()

    # Seed: Dana A grouped by email; Dana B (blank email) written ungrouped via frozen put_cell.
    dana_a = build_cells("crm_a", "a2", [("title", _role())], reader=SpyReader(), default_policy_id="default")[0]
    dana_b = build_cells("crm_b", "b2", [("job_role", _role())], reader=SpyReader(), default_policy_id="default")[0]

    groups = seed_grouping([("crm_a", "a2", "dana.osei@acme.io"), ("crm_b", "b2", "")])
    dana_pid = groups[("crm_a", "a2")]

    store.put_cell_for(dana_pid, dana_a)
    store.put_cell(dana_b)  # frozen path -> ungrouped

    assert len(store.cells_for_node(dana_pid, "role")) == 1
    assert dana_b.cell_id in {c.cell_id for c in store.ungrouped()}

    # No-clobber: an idempotent frozen re-put of the grouped cell must NOT drop its grouping.
    store.put_cell(dana_a)
    assert len(store.cells_for_node(dana_pid, "role")) == 1

    # Live re-group (Unit 3 does this under a durable-regroup capability): merge Dana B in.
    store.set_grouping(dana_b.cell_id, dana_pid)
    assert len(store.cells_for_node(dana_pid, "role")) == 2
    assert store.ungrouped() == []
