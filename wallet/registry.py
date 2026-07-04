"""The wallet's source registry — generalises `demo.py`'s `_CRMS` to four sources without
touching CRM behaviour. `fmt` is per-source config: `MultiSourceReader` builds a
`SqliteSourceReader` for `sqlite` entries and would build a `CsvSourceReader` for `csv` ones
— flipping a source back to CSV later (see the Phase 4 cut ladder) is a registry edit plus a
seed write, nothing else.
"""
from __future__ import annotations
from pathlib import Path

SEED = Path(__file__).resolve().parent.parent / "seed"

SOURCES: dict[str, dict] = {
    "crm_a": dict(key_field="id", email_field="email", policy="org_work", label="CRM A",
                 fmt="sqlite", db=str(SEED / "crm_a.db"), table="contacts"),
    "crm_b": dict(key_field="contact_id", email_field="primary_email", policy="org_work", label="CRM B",
                 fmt="sqlite", db=str(SEED / "crm_b.db"), table="contacts"),
    "personal_notes": dict(key_field="note_id", email_field=None, policy="owner_private", label="Personal notes",
                           fmt="sqlite", db=str(SEED / "personal_notes.db"), table="notes"),
    "whatsapp_calls": dict(key_field="call_id", email_field=None, policy="org_signal", label="WhatsApp calls",
                          fmt="sqlite", db=str(SEED / "whatsapp_calls.db"), table="calls"),
    "chat_history": dict(key_field="message_id", email_field=None, policy="owner_private", label="Chat History",
                         fmt="sqlite", db=str(SEED / "chat_history.db"), table="messages"),
}

ALL_SOURCES = list(SOURCES.keys())
