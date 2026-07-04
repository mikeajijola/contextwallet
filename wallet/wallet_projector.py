"""The wallet's Gate-1 projector.

TRAP #3 (pre-solved, see the build brief): predicates receive `(cap, ctx)` only — they
cannot see the cell. `wallet_visible`/`wallet_deref_source_only` (resolution/gate.py) read
`ctx['_cell_source']` / `ctx['_cell_row']`, so this subclass injects those two keys into a
COPY of ctx per cell before every `gate.check` call. Otherwise identical to
`resolution.projection.Projector.project` — the engine Projector itself is untouched.
"""
from __future__ import annotations

from contract import Capability, Cell, Context, ProjectedCell, ProjectedTable
from locators import parse_locator
from resolution.projection import Projector


def cell_ctx(ctx: Context, locator: str) -> Context:
    """Copy `ctx` with `_cell_source`/`_cell_row` injected for one cell's locator. Shared by
    `WalletProjector` (map gate) and the wallet's fetch-time ladder wrapper (value gate) so
    both gates see the same per-cell context."""
    source, row_key, _ = parse_locator(locator)
    return {**ctx, "_cell_source": source, "_cell_row": row_key}


class WalletProjector(Projector):
    def project(self, cells: list[Cell], viewer: Capability, ctx: Context) -> ProjectedTable:
        version = self.control_plane.current_version() if self.control_plane is not None else 0
        table = ProjectedTable(for_viewer=viewer.holder, cells={})

        for cell in cells:
            policy = self.registry.get(cell.policy_id)
            per_cell_ctx = cell_ctx(ctx, cell.ref.locator)

            # 1. existence — deny -> OMIT entirely (no trace)
            if not self.gate.check(viewer, policy.see_existence, per_cell_ctx):
                self._audit(viewer, cell.cell_id, "deny", version)
                continue

            # 2/3. type & state facets, independently gated
            see_type = self.gate.check(viewer, policy.see_type, per_cell_ctx)
            see_state = self.gate.check(viewer, policy.see_state, per_cell_ctx)

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
