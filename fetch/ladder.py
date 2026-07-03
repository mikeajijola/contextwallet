"""Unit 4 — the fetch ladder: Gate 2, the value gate.

`resolve_value` is the SECOND of the two gates (hole 6): projection carried
`pcell.dereference` UNEVALUATED; here it is finally evaluated, at read time, under the
caller's current capability. Tiers, cheapest first:

  1. symbolic   — ctx['symbolic'] set -> return a Symbol token, no value read at all
                  (compute over structure without dereferencing).
  2. gate       — evaluate pcell.dereference through the Gate. Deny -> flat Refusal.
                  This runs on EVERY read, INCLUDING cache hits, so a cached value
                  re-authorises every time and a revoked/expired capability closes it.
  3. materialised — a fresh cached value (re-authorised above) is returned without a read.
  4. adapter    — otherwise read once via the adapter and cache the result.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone
from typing import Union

from contract import (
    Capability,
    Context,
    Gate,
    MaterialisedValue,
    ProjectedCell,
    Refusal,
    Symbol,
    Value,
)
from fetch.adapter import CsvAdapter
from fetch.cache import MaterialisedCache


class DefaultFetchLadder:
    def __init__(self, gate: Gate, adapter: CsvAdapter, cache: MaterialisedCache,
                 default_ttl_minutes: int = 10) -> None:
        self.gate = gate
        self.adapter = adapter
        self.cache = cache
        self.default_ttl = timedelta(minutes=default_ttl_minutes)

    def resolve_value(self, pcell: ProjectedCell, cap: Capability,
                      ctx: Context) -> Union[Value, Symbol, Refusal]:
        # 1. symbolic tier — a token, never a value; no dereference needed
        if isinstance(ctx, dict) and ctx.get("symbolic"):
            return Symbol(name=pcell.cell_id)

        # 2. gate (re-authorises on EVERY read, cache hits included)
        if pcell.dereference is None or not self.gate.check(cap, pcell.dereference, ctx):
            return Refusal()

        now = datetime.now(timezone.utc)

        # 3. materialised tier — fresh cached value, already re-authorised
        mv = self.cache.fresh(pcell.cell_id, now)
        if mv is not None:
            return mv.value

        # 4. adapter tier — read once, cache with provenance
        value = self.adapter.fetch(pcell.ref)
        self.cache.put(pcell.cell_id, MaterialisedValue(
            value=value, fetched_under=cap.id(), fetched_at=now, ttl=self.default_ttl,
            origin_policy_id=(ctx.get("policy_id", "unknown") if isinstance(ctx, dict) else "unknown"),
        ))
        return value
