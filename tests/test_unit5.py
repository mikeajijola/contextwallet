"""Unit 5 acceptance tests — the write-only, tamper-evident audit log."""
from __future__ import annotations
from datetime import datetime, timezone

from contract import AuditEntry
from db import get_conn
from audit.sink import SqliteAuditSink


def _entry(event, decision, principal="analyst", cap="cap-1", cell_id=None, version=0):
    return AuditEntry(event=event, ts=datetime(2026, 7, 1, 12, 0, 0, tzinfo=timezone.utc),
                      principal=principal, capability_id=cap, cell_id=cell_id,
                      policy_version=version, decision=decision)


def _sink():
    return SqliteAuditSink(get_conn(":memory:"))


def test_1_append_and_read_in_order():
    sink = _sink()
    sink.append(_entry("resolve", "allow"))
    sink.append(_entry("project", "allow", cell_id="c1", version=3))
    sink.append(_entry("fetch", "deny", principal="contractor"))

    entries = sink.all()
    assert [e.event for e in entries] == ["resolve", "project", "fetch"]
    assert entries[1].cell_id == "c1" and entries[1].policy_version == 3
    assert entries[2].decision == "deny"


def test_2_records_both_allow_and_deny():
    sink = _sink()
    sink.append(_entry("project", "allow"))
    sink.append(_entry("fetch", "deny"))
    decisions = [e.decision for e in sink.all()]
    assert decisions == ["allow", "deny"]
    assert len(sink.for_principal("analyst")) == 2


def test_3_cannot_store_a_value():
    # structural guarantee: AuditEntry has no value field, so a dereferenced value cannot leak
    sink = _sink()
    sink.append(_entry("fetch", "allow", cell_id="colin-role"))
    assert "value" not in AuditEntry.model_fields
    dump = "\n".join(str(tuple(r)) for r in sink.conn.execute("SELECT * FROM audit_log"))
    assert "VP Engineering" not in dump           # no dereferenced value anywhere in the log


def test_4_hash_chain_is_tamper_evident():
    sink = _sink()
    sink.append(_entry("resolve", "allow"))
    sink.append(_entry("fetch", "deny", principal="contractor"))
    sink.append(_entry("project", "allow", cell_id="c2"))
    assert sink.verify_chain() is True

    # tamper: flip a stored decision directly in the db -> chain breaks
    sink.conn.execute("UPDATE audit_log SET decision='allow' WHERE seq=2")
    sink.conn.commit()
    assert sink.verify_chain() is False


def test_5_deleting_a_row_breaks_the_chain():
    sink = _sink()
    for i in range(3):
        sink.append(_entry("classify", "allow", cell_id=f"c{i}"))
    assert sink.verify_chain() is True

    sink.conn.execute("DELETE FROM audit_log WHERE seq=2")
    sink.conn.commit()
    assert sink.verify_chain() is False           # append-only: a deletion is detectable
