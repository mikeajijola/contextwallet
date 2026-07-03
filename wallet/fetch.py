"""The wallet's fetch-time helper: wraps `DefaultFetchLadder.resolve_value` with the same
per-cell `_cell_source`/`_cell_row` ctx injection `WalletProjector` uses at project-time
(trap #3, resolution/gate.py). The gate re-evaluates `pcell.dereference` on EVERY read
(cache hits included), so a cell ctx built fresh here re-authorises every time and a
revoked/expired capability closes a previously-cached value too.
"""
from __future__ import annotations

from contract import Capability, Context, ProjectedCell, Refusal, Symbol, Value
from wallet.wallet_projector import cell_ctx


def resolve_value(ladder, pcell: ProjectedCell, cap: Capability, ctx: Context) -> Value | Symbol | Refusal:
    if pcell.ref is None:
        return Refusal()
    return ladder.resolve_value(pcell, cap, cell_ctx(ctx, pcell.ref.locator))
