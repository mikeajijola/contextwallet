"""Unit 4 — the concrete `SourceReader` over the seed CSVs.

This is the real implementation Units 2/3 stubbed. `read_value` is the RAW read; callers
must gate+audit (the fetch ladder does). Row enumeration (`list_rows`/`key_field`) is a
PRIVATE method on this concrete reader, deliberately NOT on the frozen `SourceReader`
protocol — bulk row-pull is a different capability from the bounded value-access seam, and
putting it on the shared protocol would invite the "read the column" pressure the
sample-and-drop discipline exists to resist.
"""
from __future__ import annotations
import csv
from pathlib import Path

from contract import Reference, Value
from locators import parse_locator

_SEED = Path(__file__).resolve().parent.parent / "seed"
_SOURCES = {"crm_a": ("crm_a.csv", "id"), "crm_b": ("crm_b.csv", "contact_id")}


class CsvSourceReader:
    def __init__(self, seed_dir: Path = _SEED) -> None:
        self._data: dict[str, dict] = {}
        for source, (fname, keycol) in _SOURCES.items():
            rows = list(csv.DictReader(open(seed_dir / fname)))
            self._data[source] = {
                "rows": {r[keycol]: r for r in rows},
                "fields": list(rows[0].keys()),
                "key": keycol,
            }

    # ---- frozen SourceReader protocol ----
    def list_fields(self, source: str) -> list[str]:
        return list(self._data[source]["fields"])

    def sample_field(self, source: str, field: str, n: int = 3) -> list[str]:
        vals = [r[field] for r in self._data[source]["rows"].values() if r[field].strip()]
        return vals[:n]

    def read_value(self, ref: Reference) -> Value:
        source, row_key, field = parse_locator(ref.locator)
        return self._data[source]["rows"][row_key].get(field, "")

    # ---- PRIVATE (not on the SourceReader protocol) ----
    def list_rows(self, source: str) -> list[dict]:
        return list(self._data[source]["rows"].values())

    def key_field(self, source: str) -> str:
        return self._data[source]["key"]
