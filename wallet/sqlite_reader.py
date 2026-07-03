"""The wallet's default edge reader — the simulated edges are DATABASES.

This is the post-hackathon foundation: a Postgres or live-API connector is this same
`SourceReader` protocol with a different `read_value` body, which is exactly why sqlite is
the default now. The locator triple becomes an indexed point SELECT — the database's native
fetch.
"""
from __future__ import annotations
import sqlite3
from pathlib import Path

from locators import parse_locator


class SqliteSourceReader:
    """Concrete `SourceReader` (contract) over one or more sqlite files."""

    def __init__(self, paths: dict[str, Path | str], key_cols: dict[str, str], tables: dict[str, str]):
        self.paths, self.key_cols, self.tables = paths, key_cols, tables

    def _cols(self, conn, source: str) -> set[str]:
        # whitelist fields against the REAL schema — a crafted locator must not inject SQL
        return {r[1] for r in conn.execute(f"PRAGMA table_info({self.tables[source]})")}

    def list_fields(self, source: str) -> list[str]:
        with sqlite3.connect(self.paths[source]) as c:
            return sorted(self._cols(c, source))

    def sample_field(self, source: str, field: str, n: int = 3) -> list[str]:
        with sqlite3.connect(self.paths[source]) as c:
            assert field in self._cols(c, source), f"unknown field {field!r}"
            return [str(r[0]) for r in c.execute(
                f"SELECT {field} FROM {self.tables[source]} LIMIT ?", (n,))]

    def list_rows(self, source: str) -> list[dict]:
        with sqlite3.connect(self.paths[source]) as c:
            c.row_factory = sqlite3.Row
            return [dict(r) for r in c.execute(f"SELECT * FROM {self.tables[source]}")]

    def read_value(self, ref) -> str:
        source, row_key, field = parse_locator(ref.locator)
        with sqlite3.connect(self.paths[source]) as c:
            assert field in self._cols(c, source), f"unknown field {field!r}"
            row = c.execute(
                f"SELECT {field} FROM {self.tables[source]} WHERE {self.key_cols[source]} = ?",
                (row_key,)).fetchone()
            return "" if row is None or row[0] is None else str(row[0])

    # ---- private, mirrors CsvSourceReader's non-protocol helpers ----
    def key_field(self, source: str) -> str:
        return self.key_cols[source]
