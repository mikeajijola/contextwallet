"""Shared SQLite substrate for Context Twin.

One file, `twin.db`, with tables namespaced per unit. Foreign keys are off for the
week; keep it simple. Every unit imports `get_conn` and extends `init_all` with its
own `CREATE TABLE IF NOT EXISTS` (registered below as units are built).
"""
from __future__ import annotations
import os
import sqlite3
from typing import Optional


def get_conn(path: Optional[str] = None) -> sqlite3.Connection:
    """Return a shared sqlite3 connection.

    Resolution order for the DB path: explicit `path` arg, then env `TWIN_DB`,
    then the default `twin.db`. Rows come back as `sqlite3.Row` for name access.
    """
    db_path = path or os.environ.get("TWIN_DB", "twin.db")
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = OFF;")
    return conn


def init_all(conn: sqlite3.Connection) -> None:
    """Create the tables owned by units that have been built.

    Each unit owns exactly one table and provides its own `init_*` initializer:
      - Unit 1: `cells`          -> twin_core.store.init_cells      (built)
      - Unit 2: `control_plane`  -> onboarding.* (TODO when built)
      - Unit 4: `materialised`   -> fetch.*      (TODO when built)
      - Unit 5: `audit_log`      -> audit.*      (TODO when built)

    We deliberately do NOT fabricate the other units' schemas here — they own them.
    """
    # Unit 1 — the map data layer.
    from twin_core.store import init_cells
    init_cells(conn)

    # Unit 2 — the control plane (control_plane + control_plane_meta).
    from onboarding.control_plane import init_control_plane
    init_control_plane(conn)

    # Units 4/5 register their tables here once built, e.g.:
    #   from fetch.cache import init_materialised; init_materialised(conn)
    #   from audit.sink import init_audit_log; init_audit_log(conn)

    conn.commit()
