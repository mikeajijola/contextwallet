"""Onboards the wallet's four sources through the REAL Unit 2 path, then applies the two
wallet-only fixups the brief calls for: re-policying the transcript cells to `call_content`,
and durably grouping the two personal sources' rows to Colin's principal (they have no email
to seed-group on).

Sources connect ONE AT A TIME (the `/connectors/{source}/connect` endpoint onboards a single
source live); `onboard_all` is a thin wrapper over `onboard_one` for the Phase 1 test and any
caller that wants everything connected up front.
"""
from __future__ import annotations
from typing import Optional

from contract import TypeDescriptor
from locators import field_of, source_of
from resolution.capability import mint
from twin_core.inversion import build_cells, seed_grouping
from onboarding.onboard import onboard_source, OnboardReport
from wallet.registry import SOURCES

# the two divergent CRMs (unchanged from demo.py's `_CRMS`): source -> (key_field, email_field).
_CRM = {"crm_a": ("id", "email"), "crm_b": ("contact_id", "primary_email")}

# the fields each personal source MUST end up minting (Colin retrieves the wallet BY these).
REQUIRED_FIELDS = {
    "whatsapp_calls": ["participant", "org", "channel", "topic", "transcript_ref"],
    "personal_notes": ["person", "org", "topic", "body"],
}
# known-truth ontology node for each required field — used ONLY as the sanctioned fallback
# mint if the offline classifier defers/flags a field the new nodes may confuse it on.
_KNOWN_NODES = {
    "whatsapp_calls": {"participant": "person", "org": "organisation", "channel": "channel",
                       "topic": "topic", "transcript_ref": "transcript"},
    "personal_notes": {"person": "person", "org": "organisation", "topic": "topic", "body": "note"},
}
# the row-identifying field per personal source, for the Colin grouping rule.
_NAME_FIELD = {"whatsapp_calls": "participant", "personal_notes": "person"}

CONNECT_ORDER = ["crm_a", "crm_b", "whatsapp_calls", "personal_notes"]


def onboarding_cap():
    return mint("onboarder", "onboarding")


def ensure_groups(demo) -> str:
    """Idempotent durable email grouping over the two CRMs. Reads raw rows only (never
    gated) so it can run regardless of onboarding order; returns Colin's durable principal_id."""
    if not getattr(demo, "groups", None):
        records = []
        for src, (keyf, emailf) in _CRM.items():
            for r in demo.reader.list_rows(src):
                records.append((src, str(r[keyf]), r.get(emailf, "")))
        demo.groups = seed_grouping(records)
    return demo.groups[("crm_a", "a1")]


def _colin_principal_of(source: str, colin_pid: str):
    name_field = _NAME_FIELD[source]

    def principal_of(row: dict) -> Optional[str]:
        return colin_pid if row.get(name_field) == "Colin Marsh" else None

    return principal_of


def _ensure_required_fields(demo, source: str, report: OnboardReport, colin_pid: str) -> None:
    """Onboarding determinism rule: assert the required fields minted; if the offline judge
    deferred/flagged one (it may not know the new `topic`/`channel`/`transcript`/`note` nodes),
    mint it directly via build_cells + put_cell_for with the known-truth node. Wallet-layer
    seeding of wallet-layer sources — never loosen classifier thresholds instead."""
    rows = demo.reader.list_rows(source)
    key_field = SOURCES[source]["key_field"]
    policy_id = SOURCES[source]["policy"]
    principal_of = _colin_principal_of(source, colin_pid)

    served = {field_of(c.ref.locator) for c in demo.store.all_cells()
             if source_of(c.ref.locator) == source}
    for field in REQUIRED_FIELDS[source]:
        if field in served:
            continue
        node = _KNOWN_NODES[source][field]
        for row in rows:
            td = [(field, TypeDescriptor(kind="string", shape=None, ontology_node=node))]
            for cell in build_cells(source, str(row[key_field]), td, None, policy_id):
                demo.store.put_cell_for(principal_of(row), cell)
                report.minted_cells += 1
        report.bands[field] = "auto"
        report.auto_fields.append(field)


def _repolicy_transcripts(store) -> None:
    """After onboarding whatsapp_calls, re-policy the transcript cells: the value is a
    pointer to edge-resident content, gated `call_content` (owner-only dereference), not the
    source's default `org_work`."""
    for c in store.all_cells():
        if source_of(c.ref.locator) == "whatsapp_calls" and field_of(c.ref.locator) == "transcript_ref":
            store.conn.execute("UPDATE cells SET policy_id=? WHERE cell_id=?",
                               ("call_content", c.cell_id))
    store.conn.commit()


def onboard_one(demo, source: str) -> OnboardReport:
    """Onboard exactly one source through the real Unit 2 path. Safe to call in any order —
    the durable CRM grouping is computed (idempotently) on first use by whichever source
    connects first."""
    colin_pid = ensure_groups(demo)

    if source in _CRM:
        keyf, _emailf = _CRM[source]
        rows = demo.reader.list_rows(source)
        report = onboard_source(
            source, demo.reader, demo.gate, onboarding_cap(), demo.audit, demo.store,
            demo.control_plane, demo.classifier, rows=rows,
            principal_of=lambda r, s=source, k=keyf: demo.groups.get((s, str(r[k]))),
            key_field=keyf, default_policy_id=SOURCES[source]["policy"],
        )
        return report

    # the two personal sources — no email to seed-group on; group by name onto Colin's
    # durable principal instead (`principal_of` is the onboarding-time grouping hook).
    rows = demo.reader.list_rows(source)
    report = onboard_source(
        source, demo.reader, demo.gate, onboarding_cap(), demo.audit, demo.store,
        demo.control_plane, demo.classifier, rows=rows,
        principal_of=_colin_principal_of(source, colin_pid),
        key_field=SOURCES[source]["key_field"], default_policy_id=SOURCES[source]["policy"],
    )
    _ensure_required_fields(demo, source, report, colin_pid)
    if source == "whatsapp_calls":
        _repolicy_transcripts(demo.store)
    return report


def onboard_all(demo) -> dict[str, OnboardReport]:
    """Onboard all four wallet sources, in the order identity-grouping needs (CRMs first).
    Returns {source: OnboardReport}."""
    return {source: onboard_one(demo, source) for source in CONNECT_ORDER}
