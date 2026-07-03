"""Unit 2 — the versioned, human-approved control plane (hole 10: propose-not-live).

Every non-`auto` classification and every new-node proposal is a PROPOSED row here, not
a live mutation. A human approves it; approval bumps a global policy version that Unit 5
stamps onto audit entries. New-node proposals are guarded against sprawl: the review
surface shows the three closest existing nodes first (`closest_nodes`), so "this is just
`role` again" is obvious before a redundant node is approved.
"""
from __future__ import annotations
import json
import sqlite3
from typing import Callable, Optional

from contract import ControlPlaneRow

# a closest-nodes provider (concept, k) -> [(node_name, score), ...]; supplied by the classifier
ClosestNodes = Callable[[str, int], list[tuple[str, float]]]

_CREATE = """
CREATE TABLE IF NOT EXISTS control_plane (
    id          TEXT PRIMARY KEY,
    kind        TEXT,
    payload_json TEXT,
    status      TEXT,
    approver    TEXT,
    version     INTEGER
);
CREATE TABLE IF NOT EXISTS control_plane_meta (
    k TEXT PRIMARY KEY,
    v INTEGER
);
"""


def init_control_plane(conn: sqlite3.Connection) -> None:
    conn.executescript(_CREATE)
    conn.execute(
        "INSERT OR IGNORE INTO control_plane_meta (k, v) VALUES ('policy_version', 0)"
    )
    conn.commit()


class SqliteControlPlane:
    """Concrete `ControlPlane` (contract) over the shared connection."""

    def __init__(self, conn: sqlite3.Connection,
                 closest_nodes: Optional[ClosestNodes] = None) -> None:
        self.conn = conn
        self._closest = closest_nodes
        init_control_plane(conn)

    # ---- proposals ----
    def propose(self, row: ControlPlaneRow) -> str:
        """Insert a PROPOSED row (never live). Returns the row id."""
        self.conn.execute(
            "INSERT OR REPLACE INTO control_plane (id, kind, payload_json, status, approver, version) "
            "VALUES (?, ?, ?, 'proposed', NULL, ?)",
            (row.id, row.kind, json.dumps(row.payload, sort_keys=True), self.current_version()),
        )
        self.conn.commit()
        return row.id

    def approve(self, row_id: str, approver: str) -> int:
        """Approve a proposal and bump the global policy version. Returns the new version."""
        new_version = self.current_version() + 1
        self.conn.execute(
            "UPDATE control_plane SET status='approved', approver=?, version=? WHERE id=?",
            (approver, new_version, row_id),
        )
        self.conn.execute(
            "UPDATE control_plane_meta SET v=? WHERE k='policy_version'", (new_version,)
        )
        self.conn.commit()
        return new_version

    def reject(self, row_id: str, approver: str) -> None:
        self.conn.execute(
            "UPDATE control_plane SET status='rejected', approver=? WHERE id=?",
            (approver, row_id),
        )
        self.conn.commit()

    # ---- reads ----
    def current_version(self) -> int:
        row = self.conn.execute(
            "SELECT v FROM control_plane_meta WHERE k='policy_version'"
        ).fetchone()
        return int(row[0]) if row else 0

    def get(self, row_id: str) -> Optional[ControlPlaneRow]:
        row = self.conn.execute(
            "SELECT id, kind, payload_json, status, approver, version FROM control_plane WHERE id=?",
            (row_id,),
        ).fetchone()
        if row is None:
            return None
        return ControlPlaneRow(
            id=row["id"], kind=row["kind"], payload=json.loads(row["payload_json"]),
            status=row["status"], approver=row["approver"], version=row["version"],
        )

    def status_of(self, row_id: str) -> Optional[str]:
        row = self.conn.execute(
            "SELECT status FROM control_plane WHERE id=?", (row_id,)
        ).fetchone()
        return row["status"] if row else None

    def pending(self) -> list[ControlPlaneRow]:
        rows = self.conn.execute(
            "SELECT id, kind, payload_json, status, approver, version FROM control_plane "
            "WHERE status='proposed' ORDER BY id"
        ).fetchall()
        return [ControlPlaneRow(id=r["id"], kind=r["kind"], payload=json.loads(r["payload_json"]),
                                status=r["status"], approver=r["approver"], version=r["version"])
                for r in rows]

    # ---- sprawl guard ----
    def closest_nodes(self, concept: str, k: int = 3) -> list[tuple[str, float]]:
        """The k existing nodes most similar to a proposed concept (shown FIRST on review)."""
        if self._closest is None:
            raise RuntimeError("control plane has no closest_nodes provider (pass the classifier)")
        return self._closest(concept, k)
