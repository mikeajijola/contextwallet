"""FROZEN shared contract. Do not edit without flagging the contract owner.
All five units import types and interfaces from here.

AMENDMENT 1 (deliberate, owner-sanctioned): `Resolver.resolve` carries an explicit, mandatory
`overlay: GroupingOverlay` parameter. The overlay design landed AFTER this contract was frozen,
so the seam and the real `resolution/resolver.py` had drifted; this reconciles the contract to
reality (an explicit, mandatory overlay — no ctx side-channel).

AMENDMENT 2 (deliberate, owner-sanctioned): `GroupingOverlay` is now a `Protocol` DEFINED HERE
(see the seam interfaces). It is a cross-unit seam — created per query and threaded across the
resolve->join boundary (Unit 3 owns the concrete impl, Unit 4 consumes it) — so by the same rule
that puts `SourceReader`/`Gate`/`MasterTableStore`/`AuditSink` here, its type belongs in the
contract. This replaces the earlier TYPE_CHECKING-only upward import (contract referencing a
type living UP inside a unit); the layering now points DOWN only.
"""
from __future__ import annotations
from typing import Callable, Protocol, Any, Literal, Optional, runtime_checkable
from datetime import datetime, timedelta
from pydantic import BaseModel, model_validator, ConfigDict

# ---- primitives ----
Context = dict[str, Any]            # query-time context: {"purpose": str, ...}
Value = str                         # a dereferenced value (bytes|str; keep str for the week)
class Symbol(BaseModel):            # a placeholder token returned by the symbolic tier
    name: str = "X"

# ---- A.1 / A.2 ----
class Reference(BaseModel):
    source: str
    locator: str                   # opaque handle; meaningless outside its source
    resolver: str                  # id of the adapter that can dereference this

class TypeDescriptor(BaseModel):
    kind: str
    shape: Optional[str] = None
    ontology_node: str             # MANDATORY. a Cell with null node is invalid (fail-closed).

# ---- A.8 ----
class Capability(BaseModel):
    holder: str
    purpose: str
    caveats: list[str] = []
    expiry: datetime
    def id(self) -> str:
        import hashlib, json
        return hashlib.sha256(json.dumps(self.model_dump(mode="json"), sort_keys=True).encode()).hexdigest()[:16]
CapabilityRef = str

# ---- A.3 : Predicate is a callable; policies are registered by id (see Unit 3 PolicyRegistry) ----
Predicate = Callable[[Capability, Context], bool]
class CellPolicy(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    policy_id: str                 # stored on the cell; callables live in the PolicyRegistry
    see_existence: Predicate
    see_type: Predicate
    see_state: Predicate
    dereference: Predicate

# ---- A.5 ----
class MaterialisedValue(BaseModel):
    value: Value
    fetched_under: CapabilityRef
    fetched_at: datetime
    ttl: timedelta
    origin_policy_id: str

# ---- A.4 ----
class Cell(BaseModel):
    cell_id: str
    ref: Reference
    type: TypeDescriptor
    policy_id: str                 # rehydrate CellPolicy from PolicyRegistry on load
    state: Literal["placeholder", "materialised"] = "placeholder"
    materialised: Optional[MaterialisedValue] = None
    @model_validator(mode="after")
    def _state_invariant(self):
        if (self.state == "materialised") != (self.materialised is not None):
            raise ValueError("state==materialised iff materialised is not None")
        if not self.type.ontology_node:
            raise ValueError("cell without ontology_node is invalid (fail-closed)")
        return self

# ---- A.6 ----
class ConflictValue(BaseModel):
    value: Value
    source: str
    timestamp: Optional[datetime] = None   # NULLABLE. legacy systems often lack it.

class ConflictSet(BaseModel):
    principal_id: str
    ontology_node: str
    values: list[ConflictValue]
    status: Literal["agreed", "conflict_ordered", "conflict_unordered"]
    default_selection: Optional[int] = None

# ---- A.7 ----
class ProjectedCell(BaseModel):
    model_config = ConfigDict(arbitrary_types_allowed=True)
    cell_id: str
    ref: Optional[Reference] = None
    type: Optional[TypeDescriptor] = None
    state: Optional[str] = None
    dereference: Predicate                 # carried UNEVALUATED for the fetch gate

class ProjectedTable(BaseModel):
    for_viewer: str
    cells: dict[str, ProjectedCell] = {}

# ---- A.9 ----
class OntologyNode(BaseModel):
    name: str
    description: str                        # used to embed for classification
class ClassificationProposal(BaseModel):
    source: str
    field_name: str
    proposed_node: Optional[str]
    confidence: float
    band: Literal["auto", "flag", "propose_new", "quarantine"]
    evidence: str
class ControlPlaneRow(BaseModel):
    id: str
    kind: Literal["classification", "ontology_node"]
    payload: dict[str, Any]
    status: Literal["proposed", "approved", "rejected"] = "proposed"
    approver: Optional[str] = None
    version: int = 0

# ---- A.10 ----
class AuditEntry(BaseModel):
    event: Literal["project", "fetch", "onboarding_read", "classify", "resolve", "deny"]
    ts: datetime
    principal: str
    capability_id: CapabilityRef
    cell_id: Optional[str] = None
    policy_version: int = 0
    decision: Literal["allow", "deny"]

# ---- A.11 ----
class Refusal(BaseModel):
    message: str = "not available to you"   # NEVER carries a cell-derived field

# ---- B.3 ----
class ResolvedPrincipal(BaseModel):
    principal_id: str
    member_refs: list[Reference]

# ================= SEAM INTERFACES (each unit implements what it owns) =================
class MasterTableStore(Protocol):                                    # Unit 1
    def put_cell(self, cell: Cell) -> None: ...
    def cells_for(self, principal_id: str) -> list[Cell]: ...
    def cells_for_node(self, principal_id: str, node: str) -> list[Cell]: ...
    def all_cells(self) -> list[Cell]: ...

class SourceReader(Protocol):                                        # Unit 4
    def list_fields(self, source: str) -> list[str]: ...
    def sample_field(self, source: str, field: str, n: int = 3) -> list[str]: ...
    def read_value(self, ref: Reference) -> Value: ...              # raw read; caller must gate+audit

class Gate(Protocol):                                               # Unit 3
    def check(self, cap: Capability, predicate: Predicate, ctx: Context) -> bool: ...

class PolicyRegistry(Protocol):                                    # Unit 3
    def get(self, policy_id: str) -> CellPolicy: ...

class Projector(Protocol):                                         # Unit 3
    def project(self, cells: list[Cell], viewer: Capability, ctx: Context) -> ProjectedTable: ...

@runtime_checkable
class GroupingOverlay(Protocol):                                    # seam: Unit 3 owns impl, Unit 4 consumes
    """In-memory, query-scoped grouping. Created per query, threaded through
    resolve -> project -> fetch/join, discarded when the query ends. NEVER written to the db,
    NEVER read back across queries. Carries groupings only, never values. `@runtime_checkable`
    so callers can assert structural conformance at the seam."""
    def merge(self, row_keys: list[str]) -> str: ...
    def principal_of(self, row_key: str) -> Optional[str]: ...
    def cells_for(self, store: "MasterTableStore", principal_id: str,
                  node: Optional[str] = None) -> list[Cell]: ...

class Resolver(Protocol):                                          # Unit 3
    def resolve(self, candidate_refs: list[Reference], caller: Capability, ctx: Context,
                overlay: "GroupingOverlay") -> ResolvedPrincipal | Refusal: ...

class Onboarder(Protocol):                                         # Unit 2
    def onboarding_read(self, ref: Reference, sample_n: int = 3) -> list[str]: ...
    def classify(self, field_name: str, sample: list[str]) -> ClassificationProposal: ...

class ControlPlane(Protocol):                                      # Unit 2
    def propose(self, row: ControlPlaneRow) -> str: ...
    def approve(self, row_id: str, approver: str) -> int: ...       # returns new version
    def current_version(self) -> int: ...

class FetchLadder(Protocol):                                       # Unit 4
    def resolve_value(self, pcell: ProjectedCell, cap: Capability, ctx: Context) -> Value | Symbol | Refusal: ...

class AuditSink(Protocol):                                         # Unit 5
    def append(self, entry: AuditEntry) -> None: ...
