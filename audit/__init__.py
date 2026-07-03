"""Unit 5 — Audit (the write-only, tamper-evident truth)."""
from audit.sink import SqliteAuditSink, init_audit_log

__all__ = ["SqliteAuditSink", "init_audit_log"]
