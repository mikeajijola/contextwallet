"""END-TO-END MONEY-SHOT TEST — the one test that proves the demo composes across all five
units through ONE shared overlay and one shared audit sink.

Every unit passes its own tests in isolation; this is the only test that proves the pieces
compose. It exercises the full path a judge sees, structured as the demo's actual sequence:

  * onboard two divergent CRMs through the REAL Unit 2 classifier (offline judge),
  * mint two capabilities,
  * resolve Dana LIVE across two un-merged CRMs into ONE query-scoped overlay,
  * join across both sources on the live-resolved principal (the Dana rule),
  * run the SAME query under two capabilities (value vs flat refusal — the governance climax),
  * assert the system-wide invariants END TO END (persists-nothing, no naked cached values,
    no PII on the audit log, tamper-evident chain), which unit-level tests structurally cannot.

Real implementations of all five units are wired to ONE sqlite connection and ONE AuditSink
via `demo.TwinDemo`. Only the two LLM adjudicators are the units' shipped OFFLINE fakes
(`tests.test_unit2.ValueJudge`, `tests.test_unit3.CountingSame`) so the test is offline and
reproducible. `contract.py` is untouched; no unit internals are modified to make this pass.
"""
from __future__ import annotations

import pytest

from contract import ConflictSet, Refusal, ResolvedPrincipal, Cell, Reference, TypeDescriptor
from db import get_conn
from locators import row_key_of, source_of, field_of, make_locator
from resolution.capability import mint
from resolution.gate import PredicateGate, dereference_predicate
from resolution.resolver import GroupingOverlay
from resolution.descent import SemanticDescent
from onboarding.ontology_router import ConceptRouter

from demo import TwinDemo
from tests.test_unit2 import ValueJudge          # shipped offline field judge (reads sample values)
from tests.test_unit3 import CountingSame        # shipped offline same-person adjudicator

CTX = {"purpose": "cross_source_query"}
# PII that must appear NOWHERE in the audit log (values live behind the fetch gate, never logged).
_PII = ["VP Engineering", "Director, Platform", "Head of Ops", "Operations Lead",
        "colin.marsh@stripe.com", "dana.osei@acme.io"]


class SpyGate(PredicateGate):
    """Records every (predicate, result) so we can prove the identifying read was gated."""

    def __init__(self):
        self.calls: list[tuple] = []

    def check(self, cap, predicate, ctx):
        result = super().check(cap, predicate, ctx)
        self.calls.append((predicate, result))
        return result


def _build() -> tuple[TwinDemo, SpyGate]:
    gate = SpyGate()
    demo = TwinDemo(get_conn(":memory:"), judge=ValueJudge(), adjudicator=CountingSame(same=True),
                    gate=gate)
    demo.seed_and_onboard()
    return demo, gate


def _cells_at(store, source, row_key):
    return [c for c in store.all_cells()
            if source_of(c.ref.locator) == source and row_key_of(c.ref.locator) == row_key]


def _assert_no_values(conn, forbidden):
    dump = "\n".join(str(tuple(r)) for r in conn.execute("SELECT * FROM audit_log"))
    for v in forbidden:
        assert v not in dump, f"PII leaked onto the audit log: {v!r}"


# ======================================================================================
# The money shot, end to end.
# ======================================================================================
def test_moneyshot_end_to_end():
    demo, gate = _build()
    store, conn = demo.store, demo.conn
    analyst = mint("analyst", "cross_source_query", ["clearance:hr"])
    restricted = mint("contractor", "cross_source_query", [])

    # -- Step 3 precondition: two capabilities, same purpose, differing on HR clearance --
    assert analyst.purpose == restricted.purpose == "cross_source_query"
    assert "clearance:hr" in analyst.caveats and "clearance:hr" not in restricted.caveats

    # -- Step 1: Colin durably grouped (shared email); Dana deliberately NOT (blank crm_b email) --
    colin_pid = demo.groups[("crm_a", "a1")]
    assert demo.groups[("crm_a", "a1")] == demo.groups[("crm_b", "b1")]     # Colin: ONE durable principal
    dana_pid = demo.groups[("crm_a", "a2")]
    assert ("crm_b", "b2") not in demo.groups                                # Dana b2 has no durable group
    a2n, b2n = len(_cells_at(store, "crm_a", "a2")), len(_cells_at(store, "crm_b", "b2"))
    assert a2n and b2n                                                       # both records DID onboard
    # she is unresolved at rest: her durable principal sees fewer cells than both sources hold.
    assert len(store.cells_for(dana_pid)) < a2n + b2n, "Dana is durably resolved — demo is cosmetic"
    b2_cell_ids = {c.cell_id for c in _cells_at(store, "crm_b", "b2")}
    assert b2_cell_ids <= {c.cell_id for c in store.ungrouped()}             # b2 cells sit ungrouped

    # -- Step 2: onboarded through the real Unit 2 path; `region` did NOT silently mint --
    served = {field_of(c.ref.locator) for c in store.all_cells()}
    assert {"person", "email", "role", "organisation"} <= {c.type.ontology_node for c in store.all_cells()}
    assert "region" not in served                                           # no ontology node -> not minted
    assert demo.reports["crm_b"].bands["region"] in ("deferred", "propose_new", "flag", "quarantine")

    # -- Step 5: semantic descent — concept-first routing via the REAL router --
    descent = demo.descent_query("Colin", analyst, CTX)
    assert descent.concept == "person"                                      # under-specified -> root concept
    assert descent.sources == ["crm_a", "crm_b"]                            # routed to the two CRMs
    surfaced_sources = {source_of(r.locator) for r in descent.candidate_refs}
    assert surfaced_sources == {"crm_a", "crm_b"}                           # candidates from BOTH
    assert descent.projected is not None                                    # surfaced under the projection
    # the human supplies specificity after seeing candidates: pick Dana's two records.
    dana_refs = [r for r in descent.candidate_refs if row_key_of(r.locator) in {"a2", "b2"}]
    assert {row_key_of(r.locator) for r in dana_refs} == {"a2", "b2"}

    # -- Step 4 + 6: ONE overlay; resolve Dana LIVE across both CRMs into it --
    overlay = GroupingOverlay()
    before = len(gate.calls)
    rp = demo.resolve(dana_refs, analyst, CTX, overlay)
    assert isinstance(rp, ResolvedPrincipal)
    assert len(rp.member_refs) == 2                                         # two source records -> ONE principal
    assert {row_key_of(r.locator) for r in rp.member_refs} == {"a2", "b2"}
    # the identifying read went through the gate (spied) and was allowed.
    assert any(res for pred, res in gate.calls[before:] if pred is dereference_predicate)
    # the merge landed in THIS overlay.
    assert overlay.principal_of("a2") == overlay.principal_of("b2") == rp.principal_id is not None

    # -- Step 7: cross_source_query on the live-resolved principal reads THROUGH the overlay --
    assert store.cells_for(rp.principal_id) == []                          # cold store cannot see Dana...
    dana_role = demo.cross_source_query(rp.principal_id, "role", analyst, CTX, overlay)
    assert isinstance(dana_role, ConflictSet)                              # ...but the overlay join can
    assert {v.source for v in dana_role.values} == {"crm_a", "crm_b"}      # BOTH sources represented

    # -- Steps 8-9: the governance climax — SAME query, two capabilities (Colin's role) --
    colin_analyst = demo.cross_source_query(colin_pid, "role", analyst, CTX, overlay)
    assert isinstance(colin_analyst, ConflictSet)
    assert colin_analyst.status == "conflict_ordered"                      # VP vs Director, both dated
    assert {v.source for v in colin_analyst.values} == {"crm_a", "crm_b"}
    assert colin_analyst.default_selection is not None
    assert colin_analyst.values[colin_analyst.default_selection].value == "VP Engineering"  # most-recent

    colin_restricted = demo.cross_source_query(colin_pid, "role", restricted, CTX, overlay)
    assert isinstance(colin_restricted, Refusal)                           # same query, less-privileged caller
    assert set(colin_restricted.model_dump().keys()) == {"message"}        # flat — no cell-derived field

    # -- Step 10: Dana's role is conflict_unordered (one blank timestamp) --
    assert dana_role.status == "conflict_unordered"
    assert dana_role.default_selection is None                             # nothing silently chosen
    assert {v.value for v in dana_role.values} == {"Head of Ops", "Operations Lead"}

    # -- Step 11: persists-nothing --
    def snapshot():
        return {r["cell_id"]: r["principal_id"]
                for r in conn.execute("SELECT cell_id, principal_id FROM cells")}
    after = snapshot()
    # Dana's mint-time grouping is unchanged: a2 grouped, b2 still NULL (overlay wrote nothing).
    assert all(after[cid] is None for cid in b2_cell_ids)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert not any(k in t.lower() for t in tables for k in ("same", "resolution", "grouping"))
    # a second identical run from a FRESH overlay reproduces the result (no hidden state).
    overlay2 = GroupingOverlay()
    rp2, dana_role2 = demo.resolve_then_join(dana_refs, "role", analyst, CTX, overlay2)
    assert dana_role2.status == dana_role.status
    assert {(v.source, v.value) for v in dana_role2.values} == {(v.source, v.value) for v in dana_role.values}
    assert snapshot() == after                                             # the reproduction persisted nothing

    # -- Step 12: no naked values in cache — every materialised value carries fetched_under --
    mats = conn.execute("SELECT cell_id, value, fetched_under FROM materialised").fetchall()
    assert mats, "the analyst reads should have materialised at least one value"
    assert all(r["fetched_under"] for r in mats)                           # provenance on every cached value

    # -- Step 13: no PII on the audit log (though fetch/resolve events ARE logged) --
    _assert_no_values(conn, _PII)
    events = {r["event"] for r in conn.execute("SELECT event FROM audit_log")}
    assert "resolve" in events and "project" in events                     # governance events WERE recorded

    # -- Step 14: the audit chain verifies after the full run --
    assert demo.audit.verify_chain() is True


# ======================================================================================
# Step 15 — the generality assertion: the money-shot PATH itself is source-agnostic.
# ======================================================================================
def test_moneyshot_path_is_source_agnostic():
    """There is no third real source, so register a SYNTHETIC employee source and prove a
    `customer`/`person` query routes correctly through the SAME descent+projection path the
    money shot uses — routing prunes by SOURCE via the ontology map, not a post-filter, and
    registering the source is a map edit with ZERO code change."""
    demo, _ = _build()
    analyst = mint("analyst", "cross_source_query", ["clearance:hr"])

    # register a synthetic employee source with an INVENTED column (nothing else uses it).
    emp_loc = make_locator("employees", "e1", "emp_full_name")
    demo.store.put_cell(Cell(
        cell_id="employees-e1-emp_full_name",
        ref=Reference(source="employees", locator=emp_loc, resolver="employees"),
        type=TypeDescriptor(kind="string", shape=None, ontology_node="person"),
        policy_id="role_gated", state="placeholder", materialised=None,
    ))

    # concept-to-source map as DATA: the ONLY thing that differs from the real ontology is this
    # `sources` dict. Same SemanticDescent code, same store, same projector.
    concepts = {"person": {}, "customer": {"parent": "person"}, "employee": {"parent": "person"}}
    router_emp = ConceptRouter(concepts=concepts,
                               sources={"crm_a": "customer", "crm_b": "customer", "employees": "employee"})
    descent_emp = SemanticDescent(router_emp, demo.store, projector=demo.projector)

    # a `customer` query prunes the employee source out (never consults it)...
    cust = descent_emp.query("customer Colin", analyst, CTX)
    assert cust.sources == ["crm_a", "crm_b"]
    assert "employees" not in {source_of(r.locator) for r in cust.candidate_refs}

    # ...an `employee` query prunes to the employee source only...
    emp = descent_emp.query("employee Colin", analyst, CTX)
    assert emp.sources == ["employees"]
    assert {source_of(r.locator) for r in emp.candidate_refs} == {"employees"}

    # ...and the shared parent `person` surfaces all three (hierarchy works on the e2e path).
    person = descent_emp.query("Colin", analyst, CTX)
    assert person.sources == ["crm_a", "crm_b", "employees"]

    # and the router is not hardcoded to "both CRMs": the REAL yaml (no employee source)
    # returns exactly the two CRMs — removing the source is a map edit alone.
    assert ConceptRouter.from_yaml().sources_for("person") == ["crm_a", "crm_b"]
