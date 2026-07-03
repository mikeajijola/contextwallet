"""Unit 2 — the onboarding orchestrator (lazy, source-shape-agnostic).

Any tabular source walks the same path: gated bounded sample read -> shortlist+judge
classification -> route by band. There is NO source-specific code here — the judge reading
sample values absorbs vocabulary we never tuned against, so a source we've never seen maps
its fields by exactly this path.

Lazy classification (the architect's reframe): classification is not an exhaustive ingest-
time act. We classify the CONFIDENT fraction at ingest and DEFER the ambiguous long-tail to
point-of-use (`classify_deferred`), where a human is often already in the loop. Two carve-outs:
  * Retrieval-seed fields (person/email — the fields a query finds people BY) are ALWAYS
    eager: you cannot defer the type of a field you retrieve on.
  * Bulk/autonomous path (`autonomous=True`): nobody is watching, so the judge decides alone
    over the shortlist, or the field stays unresolved and the agent proceeds without it. This
    is a known v1 boundary — we do NOT claim a human is always in the loop.

Fail-closed: only a confident judged node mints a live cell; everything else is a PROPOSED
control-plane row. Rows are used only for row_key + `principal_of` grouping; attribute VALUES
are never written into cells (see reference inversion).
"""
from __future__ import annotations
from dataclasses import dataclass, field as dc_field
from typing import Callable, Optional

from contract import (
    AuditSink, Capability, ClassificationProposal, ControlPlaneRow, Gate,
    Reference, SourceReader, TypeDescriptor,
)
from locators import make_locator
from twin_core.inversion import build_cells
from twin_core.store import SqliteMasterTableStore
from onboarding.classify import OntologyClassifier, RETRIEVAL_SEED_NODES
from onboarding.control_plane import SqliteControlPlane
from onboarding.reader_gate import onboarding_read


@dataclass
class OnboardReport:
    source: str
    bands: dict[str, str] = dc_field(default_factory=dict)               # field -> band
    proposed_nodes: dict[str, Optional[str]] = dc_field(default_factory=dict)
    proposals: dict[str, str] = dc_field(default_factory=dict)           # field -> control_plane id
    auto_fields: list[str] = dc_field(default_factory=list)
    deferred: list[str] = dc_field(default_factory=list)                 # long-tail, classified on use
    minted_cells: int = 0

    def is_auto(self, field: str) -> bool:
        return self.bands.get(field) == "auto"


def _schema_ref(source: str, field: str) -> Reference:
    return Reference(source=source, locator=make_locator(source, "__schema__", field), resolver=source)


def _mint_auto(store, source, rows, principal_of, key_field, field, node,
               default_policy_id, report) -> None:
    td = [(field, TypeDescriptor(kind="string", shape=None, ontology_node=node))]
    for row in rows:
        pid = principal_of(row)
        row_key = str(row[key_field])
        for cell in build_cells(source, row_key, td, None, default_policy_id):
            store.put_cell_for(pid, cell)
            report.minted_cells += 1


def _route(proposal: ClassificationProposal, source, field, store, control_plane,
           rows, principal_of, key_field, default_policy_id, report) -> None:
    report.bands[field] = proposal.band
    report.proposed_nodes[field] = proposal.proposed_node
    if proposal.band == "auto":
        report.auto_fields.append(field)
        _mint_auto(store, source, rows, principal_of, key_field, field,
                   proposal.proposed_node, default_policy_id, report)
    else:
        report.proposals[field] = control_plane.propose(ControlPlaneRow(
            id=f"{source}:{field}", kind="classification",
            payload={"source": source, "field": field, "band": proposal.band,
                     "proposed_node": proposal.proposed_node, "evidence": proposal.evidence}))


def onboard_source(source: str, reader: SourceReader, gate: Gate, cap: Capability,
                   audit: AuditSink, store: SqliteMasterTableStore,
                   control_plane: SqliteControlPlane, classifier: OntologyClassifier,
                   rows: list[dict], principal_of: Callable[[dict], str], key_field: str,
                   default_policy_id: str = "default", lazy: bool = True,
                   eager_nodes: frozenset = RETRIEVAL_SEED_NODES,
                   autonomous: bool = False) -> OnboardReport:
    report = OnboardReport(source=source)

    for field in reader.list_fields(source):
        sample = onboarding_read(_schema_ref(source, field), reader, gate, cap, audit)

        # defer the ambiguous long-tail (retrieval-seeds + confident matches stay eager)
        if lazy and classifier.should_defer(field, sample, eager_nodes):
            report.deferred.append(field)
            report.bands[field] = "deferred"
            continue

        proposal = classifier.classify(field, sample, source=source, autonomous=autonomous)
        _route(proposal, source, field, store, control_plane, rows, principal_of,
               key_field, default_policy_id, report)

    return report


def classify_deferred(field: str, sample: list[str], source: str,
                      classifier: OntologyClassifier, store, control_plane,
                      rows: list[dict], principal_of: Callable[[dict], str], key_field: str,
                      default_policy_id: str = "default",
                      autonomous: bool = False) -> ClassificationProposal:
    """Point-of-use classification for a deferred field: same shortlist+judge path, run when a
    query actually surfaces the field. Mints if the judge is confident, else proposes."""
    report = OnboardReport(source=source)
    proposal = classifier.classify(field, sample, source=source, autonomous=autonomous)
    _route(proposal, source, field, store, control_plane, rows, principal_of,
           key_field, default_policy_id, report)
    return proposal


class Onboarder:
    """Concrete `Onboarder` (contract) — binds reader/gate/cap/audit + classifier to a source."""

    def __init__(self, reader: SourceReader, gate: Gate, cap: Capability,
                 audit: AuditSink, classifier: OntologyClassifier, source: str = "") -> None:
        self.reader, self.gate, self.cap = reader, gate, cap
        self.audit, self.classifier, self.source = audit, classifier, source

    def onboarding_read(self, ref: Reference, sample_n: int = 3) -> list[str]:
        return onboarding_read(ref, self.reader, self.gate, self.cap, self.audit, sample_n)

    def classify(self, field_name: str, sample: list[str]) -> ClassificationProposal:
        return self.classifier.classify(field_name, sample, source=self.source)
