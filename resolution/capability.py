"""Unit 3 — capability minting. The demo's "who is asking" object.

Short expiry by default (60 min). Two demo capabilities let the SAME query produce a value
for one caller and a flat refusal for the other — the governance climax.
"""
from __future__ import annotations
from datetime import datetime, timedelta, timezone

from contract import Capability


def mint(holder: str, purpose: str, caveats: list[str] | None = None,
         ttl_minutes: int = 60) -> Capability:
    return Capability(
        holder=holder,
        purpose=purpose,
        caveats=list(caveats or []),
        expiry=datetime.now(timezone.utc) + timedelta(minutes=ttl_minutes),
    )


def demo_analyst_cap() -> Capability:
    """Permissive: cross-source query WITH HR clearance -> can dereference."""
    return mint("analyst", "cross_source_query", ["clearance:hr"])


def demo_restricted_cap() -> Capability:
    """Restricted: same purpose, NO HR clearance -> gets a flat refusal on dereference."""
    return mint("contractor", "cross_source_query", [])
