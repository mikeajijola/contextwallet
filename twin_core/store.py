"""Unit 1 — SQLite backing for the master table of reference-cells.

The store holds refs + types only. A `MaterialisedValue` appears on a cell ONLY when
Unit 4 later writes a governed cache entry back through `put_cell`. The store never
fetches and never holds policy callables — a cell carries `policy_id`; the callable
`CellPolicy` is rehydrated from Unit 3's `PolicyRegistry` at use time.

Identity grouping is a SEPARATE, OVERRIDABLE column, NOT encoded in the opaque locator.
Grouping is set at write time (`put_cell_for`) and can be re-grouped live by query-time
resolution (`set_grouping`). The frozen `put_cell(cell)` writes a cell ungrouped. This
keeps the locator honestly opaque (see locators.py) and makes live resolution a genuine
per-query act rather than a read-back of a pre-baked grouping.
"""
from __future__ import annotations
import sqlite3
from typing import Optional

from contract import (
    Cell,
    MaterialisedValue,
    Reference,
    TypeDescriptor,
)

_CREATE_CELLS = """
CREATE TABLE IF NOT EXISTS cells (
    cell_id       TEXT PRIMARY KEY,
    principal_id  TEXT,              -- OVERRIDABLE grouping; set at write time, NULL = ungrouped.
    source        TEXT,             -- Reference.source
    locator       TEXT,             -- Reference.locator (opaque; source:row_key:field)
    resolver      TEXT,             -- Reference.resolver
    kind          TEXT,             -- TypeDescriptor.kind
    shape         TEXT,             -- TypeDescriptor.shape (nullable)
    ontology_node TEXT,             -- TypeDescriptor.ontology_node (mandatory, fail-closed)
    policy_id     TEXT,
    state         TEXT,             -- 'placeholder' | 'materialised'
    mat_json      TEXT              -- serialised MaterialisedValue, or NULL
);
"""


def init_cells(conn: sqlite3.Connection) -> None:
    """Create the `cells` table if it does not already exist."""
    conn.execute(_CREATE_CELLS)
    conn.commit()


class SqliteMasterTableStore:
    """Concrete `MasterTableStore` (see contract.py) over a shared sqlite connection.

    Implements the four frozen Protocol methods plus internal write paths for grouping:
    `put_cell_for` (grouping known at write time), `set_grouping` (query-time re-group),
    `ungrouped` / `principals` / `all_with_grouping` (grouping-aware reads).
    """

    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn
        init_cells(conn)

    # ---- writes ----
    def put_cell(self, cell: Cell) -> None:
        """Frozen-contract write for cell-only callers: writes the cell UNGROUPED.

        Grouping is a separate concern; a caller who does not know the principal must
        not invent one. Routes to `put_cell_for(None, cell)`. A subsequent grouped
        write (or `set_grouping`) attaches identity later.
        """
        self.put_cell_for(None, cell)

    def put_cell_for(self, principal_id: Optional[str], cell: Cell) -> None:
        """Upsert a cell with a (possibly None) grouping.

        The pydantic `Cell` validator already enforces the fail-closed invariants
        (state==materialised iff materialised is not None; non-empty ontology_node) at
        construction, so a valid `Cell` cannot violate them here. We never write raw
        rows that could bypass the model.

        Re-put semantics: on conflict we `COALESCE(new, existing)` for the grouping, so
        a real `principal_id` overrides but a `None` (e.g. an idempotent `put_cell`
        re-mint) never clobbers a grouping already set. Use `set_grouping` to change a
        grouping explicitly, including clearing it.
        """
        mat_json = cell.materialised.model_dump_json() if cell.materialised is not None else None
        self.conn.execute(
            """
            INSERT INTO cells (cell_id, principal_id, source, locator, resolver,
                               kind, shape, ontology_node, policy_id, state, mat_json)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(cell_id) DO UPDATE SET
                principal_id=COALESCE(excluded.principal_id, cells.principal_id),
                source=excluded.source,
                locator=excluded.locator,
                resolver=excluded.resolver,
                kind=excluded.kind,
                shape=excluded.shape,
                ontology_node=excluded.ontology_node,
                policy_id=excluded.policy_id,
                state=excluded.state,
                mat_json=excluded.mat_json
            """,
            (
                cell.cell_id,
                principal_id,
                cell.ref.source,
                cell.ref.locator,
                cell.ref.resolver,
                cell.type.kind,
                cell.type.shape,
                cell.type.ontology_node,
                cell.policy_id,
                cell.state,
                mat_json,
            ),
        )
        self.conn.commit()

    def set_grouping(self, cell_id: str, principal_id: Optional[str]) -> None:
        """Re-group a single cell (query-time resolution override / durable re-group).

        Unit 3 calls this ONLY under a capability that authorises durable re-grouping;
        by default query-time resolution keeps its merge in an in-memory overlay and
        persists nothing. Passing None clears the grouping (returns the cell to
        ungrouped). No-op if the cell_id is unknown.
        """
        self.conn.execute(
            "UPDATE cells SET principal_id = ? WHERE cell_id = ?", (principal_id, cell_id)
        )
        self.conn.commit()

    # ---- reads ----
    def cells_for(self, principal_id: str) -> list[Cell]:
        rows = self.conn.execute(
            "SELECT * FROM cells WHERE principal_id = ? ORDER BY cell_id", (principal_id,)
        ).fetchall()
        return [self._row_to_cell(r) for r in rows]

    def cells_for_node(self, principal_id: str, node: str) -> list[Cell]:
        """Cells for one principal classified to one ontology node.

        Returns the multiplicity that becomes a conflict downstream (e.g. two `role`
        cells for Colin — one from crm_a, one from crm_b). Reads the grouping COLUMN,
        never the locator.
        """
        rows = self.conn.execute(
            "SELECT * FROM cells WHERE principal_id = ? AND ontology_node = ? ORDER BY cell_id",
            (principal_id, node),
        ).fetchall()
        return [self._row_to_cell(r) for r in rows]

    def all_cells(self) -> list[Cell]:
        rows = self.conn.execute("SELECT * FROM cells ORDER BY cell_id").fetchall()
        return [self._row_to_cell(r) for r in rows]

    def ungrouped(self) -> list[Cell]:
        """Cells not yet resolved to a principal (grouping column IS NULL)."""
        rows = self.conn.execute(
            "SELECT * FROM cells WHERE principal_id IS NULL ORDER BY cell_id"
        ).fetchall()
        return [self._row_to_cell(r) for r in rows]

    def principals(self) -> list[str]:
        """Distinct non-null groupings currently in the map (sorted, stable)."""
        rows = self.conn.execute(
            "SELECT DISTINCT principal_id FROM cells "
            "WHERE principal_id IS NOT NULL ORDER BY principal_id"
        ).fetchall()
        return [r["principal_id"] for r in rows]

    def all_with_grouping(self) -> list[tuple[Optional[str], Cell]]:
        """Every cell paired with its (possibly None) grouping — for the demo grid."""
        rows = self.conn.execute("SELECT * FROM cells ORDER BY cell_id").fetchall()
        return [(r["principal_id"], self._row_to_cell(r)) for r in rows]

    # ---- rehydration ----
    def _row_to_cell(self, row: sqlite3.Row) -> Cell:
        """Rebuild a `Cell` from a row so the model validator re-checks invariants on load."""
        materialised: Optional[MaterialisedValue] = None
        if row["mat_json"] is not None:
            materialised = MaterialisedValue.model_validate_json(row["mat_json"])
        return Cell(
            cell_id=row["cell_id"],
            ref=Reference(
                source=row["source"],
                locator=row["locator"],
                resolver=row["resolver"],
            ),
            type=TypeDescriptor(
                kind=row["kind"],
                shape=row["shape"],
                ontology_node=row["ontology_node"],
            ),
            policy_id=row["policy_id"],
            state=row["state"],
            materialised=materialised,
        )
