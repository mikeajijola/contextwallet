"""Unit 3 — the semantic descent: the read-side mirror of classification.

A query resolves in three ordered steps (using "find Colin"):

  1. RESOLVE the query to a concept.  "Colin" -> person; "customer Colin" -> customer.
     Meaning first, before any data is touched (ConceptRouter.resolve_concept).
  2. DESCEND concept -> sources.  The ontology knows which sources instantiate the concept;
     the caller names a concept, not sources, and the router routes (ConceptRouter.sources_for).
     Specificity in the question prunes the SEARCH SPACE here — the employee Colin is never
     surfaced for a `customer` query because the descent never consulted the employee source.
  3. SURFACE candidate cells under the caller's capability/projection. A source the caller
     cannot see does not surface; values stay behind the dereference gate (Projector = Gate 1).

Two disambiguations are stacked and ORDERED and must not be collapsed:
  * concept-disambiguation (customer vs employee Colin) — runs HERE, first, narrowing which
    Colins are in scope by pruning sources. Semantic.
  * entity-resolution (is CRM-A's Colin CRM-B's Colin) — runs SECOND, over the surfaced set,
    live/banded, into a query-scoped overlay. That is `Resolver.resolve` (resolver.py), which
    the caller runs on this descent's candidate refs. This module deliberately stops at the
    surfaced candidates so the order (concept-first, identity-second) is structural.

Under-specified queries nominate, they do not guess: "Colin" resolves only to `person`, which
lives in multiple sources, so ALL are surfaced, typed by source — the human supplies the
missing specificity after seeing candidates. Explicit qualifiers route directly.
"""
from __future__ import annotations
from typing import Optional

from pydantic import BaseModel

from contract import Capability, Cell, Context, ProjectedTable, Reference
from locators import source_of
from onboarding.ontology_router import ConceptRouter
from resolution.projection import Projector

# The classifier node that a person-query surfaces. Concepts (customer/employee) are query
# meanings that live in specific SOURCES; the identifying cells in those sources are all
# classified to this node, so the descent surfaces this node from the routed sources.
IDENTIFYING_NODE = "person"


class DescentResult(BaseModel):
    """What a semantic descent returns before entity resolution runs over it."""
    model_config = {"arbitrary_types_allowed": True}
    query: str
    concept: str                 # the concept the query resolved to
    name: str                    # the identifying token ("Colin"); "" if none given
    under_specified: bool        # True if the concept straddles >1 concept among its sources
    sources: list[str]           # sources the descent consulted (concept-routed, source-pruned)
    candidate_refs: list[Reference]      # person-cells surfaced from those sources
    projected: Optional[ProjectedTable] = None   # candidates under the caller's projection


class SemanticDescent:
    """Runs query -> concept -> sources -> candidate cells. Persists nothing.

    The concept step is REAL, not a stub: it walks the ontology router every time. On the
    two-CRM demo it is a degenerate instance (one concept, both sources under it) and returns
    both CRMs — but the day a source holding a different concept is registered, the same code
    prunes it out with no rewrite (see the generality test).
    """

    def __init__(self, router: ConceptRouter, store,
                 projector: Optional[Projector] = None) -> None:
        self.router = router
        self.store = store
        self.projector = projector

    def query(self, text: str, caller: Optional[Capability] = None,
              ctx: Optional[Context] = None) -> DescentResult:
        # 1. resolve query -> concept (meaning first)
        concept, name = self.router.resolve_concept(text)

        # 2. descend concept -> sources (specificity prunes the source set BEFORE data)
        sources = self.router.sources_for(concept)
        source_set = set(sources)
        under_specified = self.router.is_concept_ambiguous(concept)

        # 3. surface candidate cells from ONLY the routed sources. This is source-pruning,
        #    not post-filtering: a cell in an un-routed source is never even considered.
        candidates: list[Cell] = [
            c for c in self.store.all_cells()
            if c.type.ontology_node == IDENTIFYING_NODE
            and source_of(c.ref.locator) in source_set
        ]
        candidate_refs = [c.ref for c in candidates]

        projected: Optional[ProjectedTable] = None
        if self.projector is not None and caller is not None:
            # projection is Gate 1: a source/cell the caller cannot see simply does not
            # surface; values stay behind the (unevaluated) dereference gate.
            projected = self.projector.project(candidates, caller, ctx or {})

        return DescentResult(
            query=text, concept=concept, name=name, under_specified=under_specified,
            sources=sources, candidate_refs=candidate_refs, projected=projected,
        )
