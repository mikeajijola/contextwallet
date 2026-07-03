"""Unit 1 — thin `MasterTable` convenience over the store.

Pure reads for the demo UI (Units 3/4 render the grid). No values are dereferenced;
each grid cell is a placeholder/materialised `Cell` describing a ref + type. Grouping
is read from the store's grouping COLUMN, never inferred from the opaque locator.
"""
from __future__ import annotations

from contract import Cell
from twin_core.store import SqliteMasterTableStore


class MasterTable:
    """Read-only view of the map: grouped principals as rows, ontology nodes as columns."""

    def __init__(self, store: SqliteMasterTableStore) -> None:
        self.store = store

    def rows(self) -> list[str]:
        """Distinct grouped principal_ids present in the map (sorted, stable)."""
        return self.store.principals()

    def columns(self) -> list[str]:
        """Distinct ontology nodes present in the map (sorted, stable)."""
        nodes = {cell.type.ontology_node for cell in self.store.all_cells()}
        return sorted(nodes)

    def grid(self) -> dict[str, dict[str, list[Cell]]]:
        """`{principal_id: {ontology_node: [Cell, ...]}}` for grouped cells only.

        A node may map to more than one cell for a principal (the multiplicity that
        becomes a conflict downstream), so values are lists. Ungrouped cells are not
        rows yet; get them via `store.ungrouped()`.
        """
        out: dict[str, dict[str, list[Cell]]] = {}
        for principal_id, cell in self.store.all_with_grouping():
            if principal_id is None:
                continue
            node = cell.type.ontology_node
            out.setdefault(principal_id, {}).setdefault(node, []).append(cell)
        return out

    def ungrouped(self) -> list[Cell]:
        """Cells not yet resolved to a principal (awaiting query-time resolution)."""
        return self.store.ungrouped()
