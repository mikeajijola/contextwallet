"""Tests for the two integration fixes the money-shot e2e surfaced (composition bugs that
unit-level tests structurally could not catch):

  Fix 1 — `PolicyRegistry.get` fails CLOSED on an unknown policy_id (deny-all, never KeyError),
          and a real `"default"` policy is registered so the mint path is intentional.
  Fix 2 — the overlay is threaded through `resolve` as an explicit, MANDATORY parameter; the
          `ctx["overlay"]` side-channel is deleted; `contract.py` is amended to match.
"""
from __future__ import annotations
from pathlib import Path

import pytest

from contract import Cell, Reference, TypeDescriptor, Refusal
from contract import GroupingOverlay as GroupingOverlayProtocol
from db import get_conn
from locators import make_locator
from twin_core.store import SqliteMasterTableStore
from resolution.gate import PredicateGate, PolicyRegistry, DEFAULT, DENY_ALL
from resolution.resolver import Resolver, GroupingOverlay
from resolution.capability import demo_analyst_cap
from fetch.source_reader import CsvSourceReader
from fetch.adapter import CsvAdapter
from fetch.cache import MaterialisedCache
from fetch.ladder import DefaultFetchLadder
from fetch.join import cross_source_query

ROOT = Path(__file__).resolve().parent.parent
CTX = {"purpose": "cross_source_query"}


# ===================================================================== Fix 1
def test_registry_get_unknown_fails_closed_not_keyerror():
    reg = PolicyRegistry()
    cap, ctx = demo_analyst_cap(), CTX          # a MAXIMALLY privileged caller...

    pol = reg.get("nonexistent-policy-id")       # ...still gets deny-all on an unknown id

    # all four facets evaluate False — the cell is invisible to projection AND unreadable at fetch
    assert pol.see_existence(cap, ctx) is False
    assert pol.see_type(cap, ctx) is False
    assert pol.see_state(cap, ctx) is False
    assert pol.dereference(cap, ctx) is False
    assert reg.unknown_lookups == 1              # the gap is observable, not silent


def test_registered_default_policy_is_pinned():
    """The `default` policy is REGISTERED (not a fallback) and behaves as specified:
    visible in the map (existence/type/state) but NOT dereferenceable without a grant."""
    reg = PolicyRegistry()
    cap, ctx = demo_analyst_cap(), CTX           # even an HR-cleared analyst cannot deref default

    pol = reg.get("default")
    assert pol is DEFAULT                         # the real registered policy, not deny-all
    assert reg.unknown_lookups == 0               # ...so it was NOT an unknown-id lookup
    assert pol.see_existence(cap, ctx) is True
    assert pol.see_type(cap, ctx) is True
    assert pol.see_state(cap, ctx) is True
    assert pol.dereference(cap, ctx) is False     # readable only with a specific grant


def _min_ladder(conn):
    reader = CsvSourceReader()
    return DefaultFetchLadder(PredicateGate(), CsvAdapter(reader), MaterialisedCache(conn)), reader


def test_cell_with_unregistered_policy_refuses_not_crashes():
    """A cell minted pointing at an unregistered policy_id, run through the REAL join, produces
    a flat Refusal (deny-all denies the read) — NOT a KeyError, NOT a served value."""
    conn = get_conn(":memory:")
    store = SqliteMasterTableStore(conn)
    pid = "grp_ghost"
    # a role cell whose ref points at a real CSV value, but whose policy_id is unregistered.
    ghost = Cell(
        cell_id="ghost-role",
        ref=Reference(source="crm_a", locator=make_locator("crm_a", "a1", "title"), resolver="crm_a"),
        type=TypeDescriptor(kind="string", shape=None, ontology_node="role"),
        policy_id="ghost-policy", state="placeholder", materialised=None,
    )
    store.put_cell_for(pid, ghost)

    ladder, reader = _min_ladder(conn)
    reg = PolicyRegistry()
    result = cross_source_query(store, GroupingOverlay(), pid, "role",
                                demo_analyst_cap(), CTX, ladder, reg, reader)

    assert isinstance(result, Refusal)            # fail-closed all the way through the join
    assert reg.unknown_lookups >= 1               # the unknown policy_id was observed
    # and the real value never surfaced anywhere in the result
    assert set(result.model_dump().keys()) == {"message"}


# ===================================================================== Fix 2
def _resolver():
    store = SqliteMasterTableStore(get_conn(":memory:"))
    return Resolver(PredicateGate(), CsvSourceReader(), store, adjudicator=lambda a, b: {"same": True})


def test_resolve_requires_explicit_overlay_missing_is_typeerror():
    r = _resolver()
    with pytest.raises(TypeError):                 # overlay is a mandatory positional parameter
        r.resolve([], demo_analyst_cap(), CTX)     # noqa: missing overlay on purpose


def test_resolve_explicit_none_overlay_is_loud_error():
    r = _resolver()
    with pytest.raises(ValueError):                # passing None is loud, never a silent no-overlay run
        r.resolve([], demo_analyst_cap(), CTX, None)


# ===================================================================== Seam promotion
def test_grouping_overlay_is_a_contract_seam_protocol():
    """`GroupingOverlay` is now a Protocol OWNED BY the contract: the seam is contract-typed,
    not implementation-typed. The concrete Unit 3 impl conforms structurally, and any object
    with the three seam methods is accepted where the contract type is expected."""
    from resolution.resolver import GroupingOverlay as ConcreteOverlay

    # the concrete Unit 3 implementation satisfies the contract seam Protocol (points DOWN).
    assert isinstance(ConcreteOverlay(), GroupingOverlayProtocol)

    # a duck-typed overlay implementing the three methods is accepted at the seam...
    class FakeOverlay:
        def merge(self, row_keys): return "ovl_fake"
        def principal_of(self, row_key): return None
        def cells_for(self, store, principal_id, node=None): return []
    assert isinstance(FakeOverlay(), GroupingOverlayProtocol)

    # ...and an object missing a seam method is NOT — proving it's a real conformance check.
    class NotAnOverlay:
        def merge(self, row_keys): return "x"
    assert not isinstance(NotAnOverlay(), GroupingOverlayProtocol)


def test_no_ctx_overlay_side_channel_remains_anywhere():
    """Grep guard: zero readers of a `ctx['overlay']` / `ctx.get('overlay')` side-channel in the
    production code. One way to pass an overlay, and it's the visible explicit parameter."""
    offenders = []
    for py in ROOT.rglob("*.py"):
        if ".venv" in py.parts or py.parent.name == "tests":
            continue
        text = py.read_text()
        if 'ctx["overlay"]' in text or "ctx['overlay']" in text or 'ctx.get("overlay")' in text \
                or "ctx.get('overlay')" in text:
            offenders.append(str(py.relative_to(ROOT)))
    assert offenders == [], f"overlay side-channel still read in: {offenders}"
