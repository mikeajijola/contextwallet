"""Unit 5 — the audit sink. The ONLY thing that persists, and it is WRITE-ONLY.

Principle 4: the audit log is append-only truth. It is never read back to shortcut a
decision (resolution/fetch must re-derive; they never consult the log to skip work). Reads
here exist only for inspection/display and verification.

Two structural guarantees:
  * No value can leak — `AuditEntry` (frozen contract) has no value field; the sink stores
    only cell_id + decision + provenance, never a dereferenced value.
  * Tamper-evident — each row carries a hash chained from the previous row, so any edit or
    deletion breaks `verify_chain()`. Append-only is enforced by construction, not by trust.
"""
from __future__ import annotations
import hashlib
import json
import sqlite3
from datetime import datetime
from typing import Optional

from contract import AuditEntry

_GENESIS = "0" * 64

_CREATE = """
CREATE TABLE IF NOT EXISTS audit_log (
    seq            INTEGER PRIMARY KEY AUTOINCREMENT,
    event          TEXT,
    ts             TEXT,
    principal      TEXT,
    capability_id  TEXT,
    cell_id        TEXT,
    policy_version INTEGER,
    decision       TEXT,
    prev_hash      TEXT,
    entry_hash     TEXT
);
"""


def init_audit_log(conn: sqlite3.Connection) -> None:
    conn.execute(_CREATE)
    conn.commit()


def _payload(entry: AuditEntry, prev_hash: str) -> str:
    body = entry.model_dump(mode="json")
    return json.dumps({"prev": prev_hash, "entry": body}, sort_keys=True)


def _hash(entry: AuditEntry, prev_hash: str) -> str:
    return hashlib.sha256(_payload(entry, prev_hash).encode()).hexdigest()


class SqliteAuditSink:
    """Concrete `AuditSink` (contract). Append + inspect only — never read to shortcut logic."""

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        init_audit_log(conn)

    # ---- the one write path ----
    def append(self, entry: AuditEntry) -> None:
        prev = self._last_hash()
        entry_hash = _hash(entry, prev)
        self.conn.execute(
            "INSERT INTO audit_log "
            "(event, ts, principal, capability_id, cell_id, policy_version, decision, prev_hash, entry_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (entry.event, entry.ts.isoformat(), entry.principal, entry.capability_id,
             entry.cell_id, entry.policy_version, entry.decision, prev, entry_hash),
        )
        self.conn.commit()

    # ---- inspection (display/verification only) ----
    def _row_to_entry(self, row: sqlite3.Row) -> AuditEntry:
        return AuditEntry(
            event=row["event"], ts=datetime.fromisoformat(row["ts"]), principal=row["principal"],
            capability_id=row["capability_id"], cell_id=row["cell_id"],
            policy_version=row["policy_version"], decision=row["decision"],
        )

    def all(self) -> list[AuditEntry]:
        rows = self.conn.execute("SELECT * FROM audit_log ORDER BY seq").fetchall()
        return [self._row_to_entry(r) for r in rows]

    def tail(self, n: int = 10) -> list[AuditEntry]:
        rows = self.conn.execute(
            "SELECT * FROM audit_log ORDER BY seq DESC LIMIT ?", (n,)
        ).fetchall()
        return [self._row_to_entry(r) for r in reversed(rows)]

    def for_principal(self, principal: str) -> list[AuditEntry]:
        rows = self.conn.execute(
            "SELECT * FROM audit_log WHERE principal = ? ORDER BY seq", (principal,)
        ).fetchall()
        return [self._row_to_entry(r) for r in rows]

    def verify_chain(self) -> bool:
        """Recompute the hash chain; False if any row was edited, inserted, or deleted."""
        prev = _GENESIS
        for row in self.conn.execute("SELECT * FROM audit_log ORDER BY seq").fetchall():
            if row["prev_hash"] != prev:
                return False
            recomputed = _hash(self._row_to_entry(row), prev)
            if recomputed != row["entry_hash"]:
                return False
            prev = row["entry_hash"]
        return True

    def _last_hash(self) -> str:
        row = self.conn.execute(
            "SELECT entry_hash FROM audit_log ORDER BY seq DESC LIMIT 1"
        ).fetchone()
        return row["entry_hash"] if row else _GENESIS
