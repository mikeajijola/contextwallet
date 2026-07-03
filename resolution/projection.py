"""Unit 3 — projection: Gate 1, the map gate.

Projection decides, per cell and per viewer, what of the MAP is visible: existence, type,
state. It NEVER evaluates `dereference` and NEVER returns a value — the value read is a
second, separate gate at fetch time (Unit 4). That is the two-gates-two-times separation
(hole 6): `project` carries `policy.dereference` into the `ProjectedCell` UNEVALUATED.

Deny-by-dissimulation: a cell whose `see_existence` is False is OMITTED ENTIRELY — no masked
placeholder, no None entry — so the viewer cannot infer it exists. A principal/column left
with zero visible cells simply never appears (the flat `ProjectedTable.cells` gives it no
row), which also closes the completeness leak.
"""
from __future__ import annotations
from datetime import datetime, timezone
from typing import Optional

from contract import (
    AuditEntry,
    AuditSink,
    Capability,
    Cell,
    Context,
    ProjectedCell,
    ProjectedTable,
)
from resolution.gate import PredicateGate, PolicyRegistry


class Projector:
    def __init__(self, registry: PolicyRegistry, gate: PredicateGate,
                 audit: Optional[AuditSink] = None, control_plane=None) -> None:
        self.registry = registry
        self.gate = gate
        self.audit = audit
        self.control_plane = control_plane

    def project(self, cells: list[Cell], viewer: Capability, ctx: Context) -> ProjectedTable:
        version = self.control_plane.current_version() if self.control_plane is not None else 0
        table = ProjectedTable(for_viewer=viewer.holder, cells={})

        for cell in cells:
            policy = self.registry.get(cell.policy_id)

            # 1. existence — deny -> OMIT entirely (no trace)
            if not self.gate.check(viewer, policy.see_existence, ctx):
                self._audit(viewer, cell.cell_id, "deny", version)
                continue

            # 2/3. type & state facets, independently gated
            see_type = self.gate.check(viewer, policy.see_type, ctx)
            see_state = self.gate.check(viewer, policy.see_state, ctx)

            # 4/5. ref included (needed for a later fetch); dereference carried UNEVALUATED
            table.cells[cell.cell_id] = ProjectedCell(
                cell_id=cell.cell_id,
                ref=cell.ref,
                type=cell.type if see_type else None,
                state=cell.state if see_state else None,
                dereference=policy.dereference,   # <-- carried, NOT evaluated
            )
            self._audit(viewer, cell.cell_id, "allow", version)

        return table

    def _audit(self, viewer: Capability, cell_id: str, decision: str, version: int) -> None:
        if self.audit is None:
            return
        self.audit.append(AuditEntry(
            event="project", ts=datetime.now(timezone.utc), principal=viewer.holder,
            capability_id=viewer.id(), cell_id=cell_id, policy_version=version, decision=decision,
        ))
