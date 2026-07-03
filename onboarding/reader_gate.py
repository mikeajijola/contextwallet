"""Unit 2 — the ONE place classification is allowed to touch values (hole 1: sample-and-drop).

`onboarding_read` reads a BOUNDED sample of a field (default 3, never the whole column),
and only through the gate under an onboarding-scoped capability. The sample is returned to
the classifier and then DROPPED — it is never written into a Cell or the twin. Callers MUST
NOT persist it. Every read (allow or deny) is audited.
"""
from __future__ import annotations
from datetime import datetime, timezone

from contract import (
    AuditEntry,
    AuditSink,
    Capability,
    Context,
    Gate,
    Reference,
    SourceReader,
)
from locators import field_of


def onboarding_predicate(cap: Capability, ctx: Context) -> bool:
    """An onboarding read is permitted only under an onboarding-purpose capability."""
    return cap.purpose == "onboarding"


def onboarding_read(ref: Reference, reader: SourceReader, gate: Gate,
                    cap: Capability, audit: AuditSink, sample_n: int = 3) -> list[str]:
    """Gated, bounded, audited sample read. Returns [] on deny.

    The returned values are for classification ONLY and must be dropped afterwards; they
    are never retained in a Cell or anywhere in the twin (see hole 1 / test 3).
    """
    field = field_of(ref.locator)
    ctx: Context = {"purpose": "onboarding", "source": ref.source, "field": field}

    if not gate.check(cap, onboarding_predicate, ctx):
        audit.append(AuditEntry(
            event="deny", ts=datetime.now(timezone.utc), principal=ref.source,
            capability_id=cap.id(), cell_id=None, policy_version=0, decision="deny",
        ))
        return []

    sample = reader.sample_field(ref.source, field, sample_n)

    audit.append(AuditEntry(
        event="onboarding_read", ts=datetime.now(timezone.utc), principal=ref.source,
        capability_id=cap.id(), cell_id=None, policy_version=0, decision="allow",
    ))
    return sample
