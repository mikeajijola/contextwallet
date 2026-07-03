"""Unit 4 — the materialised-value cache (values MAY persist; groupings may not).

Principle 3: a cached value is still the right person's value and RE-AUTHORISES on every
read (the ladder gates before returning it), so a revocation closes it. That is why values
get a durable cache while groupings do not. This table stores the `MaterialisedValue`
provenance (fetched_under capability, fetched_at, ttl, origin_policy_id) per cell.
"""
from __future__ import annotations
import sqlite3
from datetime import datetime, timedelta
from typing import Optional

from contract import MaterialisedValue

_CREATE = """
CREATE TABLE IF NOT EXISTS materialised (
    cell_id          TEXT PRIMARY KEY,
    value            TEXT,
    fetched_under    TEXT,
    fetched_at       TEXT,      -- ISO datetime
    ttl_seconds      REAL,
    origin_policy_id TEXT
);
"""


def init_materialised(conn: sqlite3.Connection) -> None:
    conn.execute(_CREATE)
    conn.commit()


class MaterialisedCache:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        init_materialised(conn)

    def put(self, cell_id: str, mv: MaterialisedValue) -> None:
        self.conn.execute(
            "INSERT OR REPLACE INTO materialised "
            "(cell_id, value, fetched_under, fetched_at, ttl_seconds, origin_policy_id) "
            "VALUES (?, ?, ?, ?, ?, ?)",
            (cell_id, mv.value, mv.fetched_under, mv.fetched_at.isoformat(),
             mv.ttl.total_seconds(), mv.origin_policy_id),
        )
        self.conn.commit()

    def get(self, cell_id: str) -> Optional[MaterialisedValue]:
        row = self.conn.execute(
            "SELECT value, fetched_under, fetched_at, ttl_seconds, origin_policy_id "
            "FROM materialised WHERE cell_id = ?", (cell_id,)
        ).fetchone()
        if row is None:
            return None
        return MaterialisedValue(
            value=row["value"], fetched_under=row["fetched_under"],
            fetched_at=datetime.fromisoformat(row["fetched_at"]),
            ttl=timedelta(seconds=row["ttl_seconds"]), origin_policy_id=row["origin_policy_id"],
        )

    def fresh(self, cell_id: str, now: datetime) -> Optional[MaterialisedValue]:
        """A cached value whose TTL has not expired, else None (a stale entry forces a refetch)."""
        mv = self.get(cell_id)
        if mv is None:
            return None
        return mv if (mv.fetched_at + mv.ttl) > now else None
