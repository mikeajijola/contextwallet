"""Unit 4 — the cross-source join. Reads THROUGH the query-scoped overlay (the Dana rule).

`cross_source_query` assembles one principal's values for one ontology node across every
source, via the fetch ladder (each read gated), into a `ConflictSet`. It MUST read cells
through `overlay.cells_for`, NOT a cold `store.cells_for`: a live-resolved principal (Dana,
with no shared email) exists as one principal only inside the overlay this query's `resolve`
populated. resolve and this join are ONE capability-scoped call chain sharing ONE overlay —
pass the same `overlay` (and `ctx`) through both, or Dana is invisible here and the
live-resolution demo breaks.

If the caller's capability cannot dereference the values, the whole join returns a flat
`Refusal` (the governance climax: analyst gets the conflict, the restricted cap gets nothing).
"""
from __future__ import annotations
from typing import Union

from contract import (
    Capability,
    ConflictSet,
    ConflictValue,
    Context,
    ProjectedCell,
    Reference,
    Refusal,
    Symbol,
)
from locators import make_locator, parse_locator
from fetch.conflict import build_conflict, parse_ts
from fetch.ladder import DefaultFetchLadder

# per-source ordering metadata (NOT a served attribute; used only to order conflicts)
_TS_FIELD = {"crm_a": "updated_at", "crm_b": "last_touch"}


def cross_source_query(store, overlay, principal_id: str, node: str,
                       cap: Capability, ctx: Context,
                       ladder: DefaultFetchLadder, registry, reader
                       ) -> Union[ConflictSet, Refusal]:
    cells = overlay.cells_for(store, principal_id, node)   # <-- the Dana rule
    conflict_values: list[ConflictValue] = []

    for cell in cells:
        pcell = ProjectedCell(
            cell_id=cell.cell_id, ref=cell.ref, type=cell.type, state=cell.state,
            dereference=registry.get(cell.policy_id).dereference,
        )
        value = ladder.resolve_value(pcell, cap, ctx)
        if isinstance(value, Refusal):
            return Refusal()                                # caller can't dereference -> flat refusal
        if isinstance(value, Symbol):
            continue                                        # symbolic mode: no concrete conflict

        source, row_key, _ = parse_locator(cell.ref.locator)
        ts_field = _TS_FIELD.get(source)
        ts_raw = ""
        if ts_field:
            ts_raw = reader.read_value(
                Reference(source=source, locator=make_locator(source, row_key, ts_field), resolver=source)
            )
        conflict_values.append(ConflictValue(value=value, source=source, timestamp=parse_ts(ts_raw)))

    return build_conflict(principal_id, node, conflict_values)
