"""Unit 2 (ontology owner) — the concept router: the relational structure that turns the
ontology from a flat vocabulary into a map from a CONCEPT to the SOURCES that instantiate it.

This is the read-side mirror of classification. Classification (classify.py) maps a source
FIELD to an ontology NODE (write-side). The router maps a QUERY CONCEPT to SOURCES
(read-side). They meet at `person`: a source's registered concept says WHICH person-cells it
holds (customers vs employees), so a query for `customer` never even consults an employee
source — the descent went to a different node and different source, not a post-filter.

The hierarchy makes specificity prune the search space: `customer` and `employee` are kinds
of `person`. Ask for the parent `person` (under-specified) and every descendant source
surfaces; ask for the child `customer` and only customer sources do — decided BEFORE any data
is touched. Nothing about "both CRMs" is hardcoded: `sources_for` walks the ontology map, so
registering or removing a source is an edit to `ontology.yaml`'s `sources` block ONLY.
"""
from __future__ import annotations
from pathlib import Path
from typing import Optional

import yaml

_ONTOLOGY_PATH = Path(__file__).resolve().parent.parent / "ontology.yaml"


class ConceptRouter:
    """Resolves a query to a concept and descends concept -> sources via the ontology.

    Construct from the yaml file (`ConceptRouter.from_yaml()`) or from explicit maps
    (`ConceptRouter(concepts=..., sources=...)`) — the latter is how the generality test
    registers/removes a synthetic source with ZERO code change.
    """

    def __init__(self, concepts: dict[str, dict], sources: dict[str, str]) -> None:
        self._concepts = dict(concepts)
        self._sources = dict(sources)
        # every concept named as a parent, or as a source's concept, must exist as a node.
        for name, spec in self._concepts.items():
            parent = (spec or {}).get("parent")
            if parent is not None and parent not in self._concepts:
                raise ValueError(f"concept '{name}' has unknown parent '{parent}'")
        for src, concept in self._sources.items():
            if concept not in self._concepts:
                raise ValueError(f"source '{src}' maps to unknown concept '{concept}'")

    # ---- construction ----
    @classmethod
    def from_yaml(cls, path: Path = _ONTOLOGY_PATH) -> "ConceptRouter":
        data = yaml.safe_load(path.read_text())
        return cls(concepts=data.get("concepts", {}) or {},
                   sources=data.get("sources", {}) or {})

    # ---- hierarchy ----
    def is_concept(self, name: str) -> bool:
        return name in self._concepts

    def parent_of(self, concept: str) -> Optional[str]:
        return (self._concepts.get(concept) or {}).get("parent")

    def ancestors(self, concept: str) -> list[str]:
        """`concept` then each parent up to the root (inclusive of `concept`)."""
        out, cur, seen = [], concept, set()
        while cur is not None and cur not in seen:
            out.append(cur)
            seen.add(cur)
            cur = self.parent_of(cur)
        return out

    def descendants(self, concept: str) -> set[str]:
        """`concept` plus every concept that has it as an ancestor (transitive)."""
        out = set()
        for c in self._concepts:
            if concept in self.ancestors(c):
                out.add(c)
        return out

    # ---- step 1: resolve query -> concept ----
    def resolve_concept(self, query: str, root: str = "person") -> tuple[str, str]:
        """Resolve a free-text query to (concept, identifying_token).

        A leading token that names a concept is an explicit qualifier and routes directly
        ("customer Colin" -> ('customer', 'Colin')). Otherwise the query is under-specified
        and resolves to the root concept ("Colin" -> ('person', 'Colin')) — nominate, don't
        guess: it surfaces everything and lets the human add specificity after seeing
        candidates. This is the read-side of nominate-don't-decide.
        """
        parts = query.strip().split()
        if not parts:
            return (root, "")
        head = parts[0].lower()
        if self.is_concept(head) and head != root:
            return (head, " ".join(parts[1:]).strip())
        return (root, query.strip())

    # ---- step 2: descend concept -> sources ----
    def sources_for(self, concept: str) -> list[str]:
        """Sources whose registered concept is `concept` or a descendant of it.

        This IS the descent's pruning: a query for `customer` returns only customer
        sources; a query for the parent `person` returns customer AND employee sources.
        No source name is hardcoded — the answer is a walk over the ontology map, so a
        source appears here iff it is registered in `ontology.yaml`'s `sources` block.
        """
        wanted = self.descendants(concept)
        return sorted(src for src, c in self._sources.items() if c in wanted)

    def registered_sources(self) -> dict[str, str]:
        return dict(self._sources)

    def is_concept_ambiguous(self, concept: str) -> bool:
        """True if this concept has more than one distinct concept among its sources — i.e.
        an under-specified query would straddle multiple meanings (surface all, don't pick)."""
        concepts_hit = {self._sources[s] for s in self.sources_for(concept)}
        return len(concepts_hit) > 1
