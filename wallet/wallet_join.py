"""Wallet-layer cross-source join — mirrors `fetch.join.cross_source_query` but (a) resolves
values through `wallet.fetch.resolve_value` so the per-cell `_cell_source`/`_cell_row` ctx
(gate.py trap #3) is injected before every dereference check, and (b) pre-filters the
candidate cells through `WalletProjector` (existence gate) before the value loop, so a cell
invisible to this viewer is OMITTED rather than tripping the engine's all-or-nothing
refusal (leak discipline: absence leaks nothing, same principle as the map). `fetch/join.py`
is engine code and stays untouched; this is the wallet's own call site for the same
Dana-rule overlay read (`overlay.cells_for`).

In THIS seed, a cell that fails existence for a viewer under `org_work`/`owner_private`
always fails dereference too (same predicate, `wallet_deref` == `wallet_visible`) — so
pre-filtering by existence and then running the engine's per-cell dereference loop over the
survivors is equivalent to the engine behaviour, just leak-safe first.
"""
from __future__ import annotations
from typing import Union

from contract import Capability, ConflictSet, ConflictValue, Context, ProjectedCell, Reference, Refusal, Symbol
from locators import make_locator, parse_locator
from fetch.conflict import build_conflict, parse_ts
from wallet.fetch import resolve_value

# per-source ordering metadata (NOT a served attribute; used only to order conflicts)
_TS_FIELD = {"crm_a": "updated_at", "crm_b": "last_touch"}


def cross_source_query(store, overlay, principal_id: str, node: str,
                       cap: Capability, ctx: Context, ladder, registry, reader, wproj
                       ) -> Union[ConflictSet, Refusal]:
    cells = overlay.cells_for(store, principal_id, node)   # <-- the Dana rule
    projected = wproj.project(cells, cap, ctx)             # <-- leak-safe existence gate first
    visible = [c for c in cells if c.cell_id in projected.cells]

    conflict_values: list[ConflictValue] = []
    for cell in visible:
        pcell = ProjectedCell(
            cell_id=cell.cell_id, ref=cell.ref, type=cell.type, state=cell.state,
            dereference=registry.get(cell.policy_id).dereference,
        )
        value = resolve_value(ladder, pcell, cap, ctx)
        if type(value).__name__ == "Refusal":
            return Refusal()                                # visible but not derefable -> flat refusal
        if type(value).__name__ == "Symbol":
            continue

        source, row_key, _ = parse_locator(cell.ref.locator)
        ts_field = _TS_FIELD.get(source)
        ts_raw = ""
        if ts_field:
            ts_raw = reader.read_value(
                Reference(source=source, locator=make_locator(source, row_key, ts_field), resolver=source)
            )
        conflict_values.append(ConflictValue(value=value, source=source, timestamp=parse_ts(ts_raw)))

    return build_conflict(principal_id, node, conflict_values)
