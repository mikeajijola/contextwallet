"""Semantic-descent tests — concept-to-source routing (the read-side mirror of classification).

The generality test the mandate requires: there is no real third source to catch a hardcoded
"both CRMs" descent, so we register a SYNTHETIC source holding a DIFFERENT concept (`employee`,
invented columns) and prove the descent prunes by SOURCE via the ontology map, not by a
post-filter — and that removing that source is an ontology-map edit with ZERO code change.

Offline/deterministic: the descent's concept step is pure routing (no LLM, no embeddings).
"""
from __future__ import annotations

from contract import Cell, Reference, TypeDescriptor
from db import get_conn
from locators import make_locator, source_of
from twin_core.store import SqliteMasterTableStore
from resolution.descent import SemanticDescent
from resolution.projection import Projector
from resolution.gate import PredicateGate, PolicyRegistry
from resolution.capability import demo_analyst_cap
from onboarding.ontology_router import ConceptRouter


# --------------------------------------------------------------------------- fixtures
def _person_cell(src: str, rk: str, field: str, policy_id: str = "open") -> Cell:
    return Cell(
        cell_id=f"{src}-{rk}-{field}",
        ref=Reference(source=src, locator=make_locator(src, rk, field), resolver=src),
        type=TypeDescriptor(kind="string", shape=None, ontology_node="person"),
        policy_id=policy_id, state="placeholder", materialised=None,
    )


def _store_with_employee_source():
    """CRM person-cells PLUS a synthetic employee source with INVENTED columns."""
    store = SqliteMasterTableStore(get_conn(":memory:"))
    store.put_cell(_person_cell("crm_a", "a1", "full_name"))     # customer Colin
    store.put_cell(_person_cell("crm_b", "b1", "name"))          # customer Colin
    # synthetic employee source — columns nothing else uses, proving no name is hardcoded
    store.put_cell(_person_cell("employees", "e1", "emp_full_name"))  # employee Colin
    return store


# concept-to-source maps as DATA. The ONLY difference between "employee source registered"
# and "employee source removed" is these dicts — no code differs between the two routers.
_CONCEPTS = {"person": {}, "customer": {"parent": "person"}, "employee": {"parent": "person"}}
ROUTER_WITH_EMP = ConceptRouter(
    concepts=_CONCEPTS,
    sources={"crm_a": "customer", "crm_b": "customer", "employees": "employee"},
)
ROUTER_NO_EMP = ConceptRouter(
    concepts=_CONCEPTS,
    sources={"crm_a": "customer", "crm_b": "customer"},
)


def _srcs(result):
    return {source_of(r.locator) for r in result.candidate_refs}


# --------------------------------------------------------------------------- concept resolution
def test_1_resolve_concept_qualifier_vs_underspecified():
    r = ROUTER_WITH_EMP
    assert r.resolve_concept("customer Colin") == ("customer", "Colin")
    assert r.resolve_concept("employee Colin") == ("employee", "Colin")
    # under-specified: no leading concept qualifier -> the root concept, whole text kept
    assert r.resolve_concept("Colin") == ("person", "Colin")
    assert r.resolve_concept("Colin Marsh") == ("person", "Colin Marsh")
    # the root word itself is not treated as a pruning qualifier
    assert r.resolve_concept("person Colin") == ("person", "person Colin")


# --------------------------------------------------------------------------- the 3 mandated asserts
def test_2_customer_query_surfaces_crms_not_employee_source():
    """Assertion 1: a `customer` query surfaces the CRMs and NOT the employee source —
    concept routing genuinely PRUNES BY SOURCE, it is not a post-filter over all Colins."""
    d = SemanticDescent(ROUTER_WITH_EMP, _store_with_employee_source())
    res = d.query("customer Colin")

    assert res.concept == "customer"
    assert res.sources == ["crm_a", "crm_b"]        # descent consulted only customer sources
    assert _srcs(res) == {"crm_a", "crm_b"}         # ...and only their cells surfaced
    assert "employees" not in _srcs(res)            # the employee Colin was never consulted


def test_3_person_query_surfaces_all_three_via_hierarchy():
    """Assertion 2: a query for the shared parent `person` surfaces all three (hierarchy works)."""
    d = SemanticDescent(ROUTER_WITH_EMP, _store_with_employee_source())
    res = d.query("Colin")                           # under-specified -> parent concept `person`

    assert res.concept == "person"
    assert res.sources == ["crm_a", "crm_b", "employees"]
    assert _srcs(res) == {"crm_a", "crm_b", "employees"}
    assert res.under_specified is True               # straddles customer AND employee -> nominate all


def test_4_removing_source_is_ontology_map_edit_only():
    """Assertion 3: removing the fake source needs ZERO code change — only an ontology-map
    entry. Same SemanticDescent code, same store; only the router's `sources` dict differs."""
    store = _store_with_employee_source()

    with_emp = SemanticDescent(ROUTER_WITH_EMP, store).query("Colin")
    no_emp = SemanticDescent(ROUTER_NO_EMP, store).query("Colin")   # <-- only the map changed

    assert "employees" in _srcs(with_emp)
    assert "employees" not in _srcs(no_emp)          # removed by a map edit alone
    assert no_emp.sources == ["crm_a", "crm_b"]
    # and the router is not hardcoded to "both CRMs": with employee registered, `person`
    # returns three; the real yaml (no employee source) returns exactly the two CRMs.
    assert ConceptRouter.from_yaml().sources_for("person") == ["crm_a", "crm_b"]


# --------------------------------------------------------------------------- routing edge cases
def test_5_employee_query_prunes_to_employee_source_only():
    d = SemanticDescent(ROUTER_WITH_EMP, _store_with_employee_source())
    res = d.query("employee Colin")
    assert res.sources == ["employees"]
    assert _srcs(res) == {"employees"}               # specificity pruned out both CRMs


def test_6_unregistered_concept_source_surfaces_nothing():
    """On the REAL ontology no employee source is registered, so `employee X` routes to an
    empty source set — honest emptiness, not an error or a silent fallback to the CRMs."""
    store = _store_with_employee_source()
    res = SemanticDescent(ROUTER_NO_EMP, store).query("employee Colin")
    assert res.sources == []
    assert res.candidate_refs == []


def test_7_projection_gates_surfaced_candidates():
    """Step 3 surfaces under the caller's projection: a secret person-cell does not surface,
    proving capability/projection composes with concept routing (and values stay gated)."""
    store = SqliteMasterTableStore(get_conn(":memory:"))
    store.put_cell(_person_cell("crm_a", "a1", "full_name", policy_id="open"))
    store.put_cell(_person_cell("crm_b", "b1", "name", policy_id="secret"))  # existence-denied
    proj = Projector(PolicyRegistry(), PredicateGate())
    d = SemanticDescent(ROUTER_NO_EMP, store, projector=proj)

    res = d.query("customer Colin", demo_analyst_cap(), {"purpose": "cross_source_query"})

    # both are concept-routed candidates, but projection OMITS the secret one entirely
    assert _srcs(res) == {"crm_a", "crm_b"}                       # routing found both
    assert set(res.projected.cells) == {"crm_a-a1-full_name"}    # projection surfaced only open
    assert not any("secret" in cid for cid in res.projected.cells)


def test_8_hierarchy_helpers():
    r = ROUTER_WITH_EMP
    assert r.ancestors("customer") == ["customer", "person"]
    assert r.descendants("person") == {"person", "customer", "employee"}
    assert r.descendants("customer") == {"customer"}
    assert r.parent_of("customer") == "person"
    assert r.parent_of("person") is None
