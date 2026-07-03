"""Thin demo wiring harness — the money-shot entrypoint that composes the five real units.

This is the ONLY integration code the e2e reviewer writes (per the mandate): it does NOT
modify any unit's internal logic. It wires the real implementations of all five units to
ONE shared sqlite connection (`db.get_conn`) and ONE `AuditSink`, and — critically — threads
ONE `GroupingOverlay` through resolve AND the cross-source join (`resolve_then_join`). If two
overlays existed, or the join read the store cold, a live-resolved principal (Dana, no shared
email) would be invisible at join time; threading one overlay is the wiring the unit agents
flagged open post-merge.

The LLM adjudicators (resolver's same-person judge, classifier's field judge) are INJECTED by
the caller, which passes the units' shipped deterministic offline fakes so the demo is offline
and reproducible. Everything else is the real unit.
"""
from __future__ import annotations
from typing import Callable, Optional

from contract import Capability, ConflictSet, GroupingOverlay, Refusal, ResolvedPrincipal
from resolution.capability import mint
from audit.sink import SqliteAuditSink
from twin_core.store import SqliteMasterTableStore
from twin_core.inversion import seed_grouping
from onboarding.classify import OntologyClassifier, VerdictCache
from onboarding.control_plane import SqliteControlPlane
from onboarding.onboard import onboard_source, OnboardReport
from onboarding.ontology_router import ConceptRouter
from resolution.gate import PredicateGate, PolicyRegistry
from resolution.projection import Projector
from resolution.resolver import Resolver   # concrete impl; the overlay TYPE is contract.GroupingOverlay
from resolution.descent import SemanticDescent, DescentResult
from fetch.source_reader import CsvSourceReader
from fetch.adapter import CsvAdapter
from fetch.cache import MaterialisedCache
from fetch.ladder import DefaultFetchLadder
from fetch.join import cross_source_query

# the two divergent CRMs the demo lives on: (source, key column, email column).
_CRMS = [("crm_a", "id", "email"), ("crm_b", "contact_id", "primary_email")]

# onboarding runs under an onboarding-purpose capability (Gate lets the bounded sample read).
def onboarding_cap() -> Capability:
    return mint("onboarder", "onboarding")


class TwinDemo:
    """Composes the five real units on one connection + one audit sink.

    Units wired here, all real: Unit 1 store (`SqliteMasterTableStore`), Unit 2 onboarding
    (`OntologyClassifier`/`SqliteControlPlane`/`ConceptRouter`), Unit 3 gate/projection/
    resolution (`PredicateGate`/`Projector`/`Resolver`/`SemanticDescent`), Unit 4 fetch/join
    (`CsvSourceReader`/`CsvAdapter`/`MaterialisedCache`/`DefaultFetchLadder`/`cross_source_query`),
    Unit 5 audit (`SqliteAuditSink`). Only the two LLM adjudicators are injected fakes.
    """

    def __init__(self, conn, judge: Callable, adjudicator: Callable,
                 gate: Optional[PredicateGate] = None, reader: Optional[object] = None) -> None:
        self.conn = conn
        # Unit 5 — the one thing that persists (write-only, chained).
        self.audit = SqliteAuditSink(conn)
        # Unit 1 — the master table of reference-cells.
        self.store = SqliteMasterTableStore(conn)
        # Unit 2 — classifier (offline judge injected), control plane, router.
        self.classifier = OntologyClassifier(judge=judge, cache=VerdictCache(conn), audit=self.audit)
        self.control_plane = SqliteControlPlane(conn, closest_nodes=self.classifier.closest_nodes)
        self.router = ConceptRouter.from_yaml()
        # Unit 3 — ONE gate shared across projection + resolution + fetch (two call sites, one gate).
        self.gate = gate or PredicateGate()
        self.registry = PolicyRegistry()
        self.projector = Projector(self.registry, self.gate, audit=self.audit,
                                   control_plane=self.control_plane)
        # Unit 4 — real reader (CSV by default; the wallet server passes a MultiSourceReader)
        # + fetch ladder (cache on the SHARED conn so the invariant scan can inspect
        # materialised provenance).
        self.reader = reader or CsvSourceReader()
        self.resolver = Resolver(self.gate, self.reader, self.store,
                                 adjudicator=adjudicator, audit=self.audit)
        self.descent = SemanticDescent(self.router, self.store, projector=self.projector)
        self.adapter = CsvAdapter(self.reader)
        self.cache = MaterialisedCache(conn)
        self.ladder = DefaultFetchLadder(self.gate, self.adapter, self.cache)

        self.groups: dict[tuple[str, str], str] = {}
        self.reports: dict[str, OnboardReport] = {}

    # ---- setup -----------------------------------------------------------------
    def seed_and_onboard(self, default_policy_id: str = "role_gated") -> dict[tuple[str, str], str]:
        """Durable shared-email grouping over BOTH CRMs, then onboard each through the real
        Unit 2 path so cells exist with classified ontology nodes. Colin (shared email) is
        durably grouped; Dana's second record (blank email) is left ungrouped for live
        resolution. `region` (no ontology node) is NOT minted — it defers/proposes."""
        records = []
        for src, keyf, emailf in _CRMS:
            for r in self.reader.list_rows(src):
                records.append((src, str(r[keyf]), r.get(emailf, "")))
        self.groups = seed_grouping(records)

        for src, keyf, _ in _CRMS:
            rows = self.reader.list_rows(src)
            self.reports[src] = onboard_source(
                src, self.reader, self.gate, onboarding_cap(), self.audit, self.store,
                self.control_plane, self.classifier, rows=rows,
                principal_of=lambda r, s=src, k=keyf: self.groups.get((s, str(r[k]))),
                key_field=keyf, default_policy_id=default_policy_id,
            )
        return self.groups

    # ---- the money-shot call chain ---------------------------------------------
    def descent_query(self, text: str, cap: Capability, ctx: dict) -> DescentResult:
        return self.descent.query(text, cap, ctx)

    def resolve(self, refs, cap: Capability, ctx: dict, overlay: GroupingOverlay):
        return self.resolver.resolve(refs, cap, ctx, overlay)

    def cross_source_query(self, principal_id: str, node: str, cap: Capability,
                           ctx: dict, overlay: GroupingOverlay):
        # reads THROUGH overlay.cells_for (the Dana rule) — same overlay the resolve populated.
        return cross_source_query(self.store, overlay, principal_id, node, cap, ctx,
                                  self.ladder, self.registry, self.reader)

    def resolve_then_join(self, refs, node: str, cap: Capability, ctx: dict,
                          overlay: GroupingOverlay):
        """The single capability-scoped chain: resolve -> join, threading ONE overlay through
        BOTH. Returns (ResolvedPrincipal|Refusal, ConflictSet|Refusal)."""
        rp = self.resolve(refs, cap, ctx, overlay)
        if type(rp).__name__ == "Refusal":
            return rp, rp
        cs = self.cross_source_query(rp.principal_id, node, cap, ctx, overlay)
        return rp, cs
