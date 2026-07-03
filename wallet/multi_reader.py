"""The wallet's `SourceReader` built FROM the `SOURCES` registry — dispatches each of the
four protocol methods to a per-source reader chosen by that source's `fmt`. Today every
source is `sqlite`; a source flipped to `fmt: csv` in the registry is served by the same
`CsvSourceReader` the frozen engine tests use, with zero code change here.
"""
from __future__ import annotations

from locators import source_of
from fetch.source_reader import CsvSourceReader
from wallet.registry import SOURCES
from wallet.sqlite_reader import SqliteSourceReader


class MultiSourceReader:
    """Concrete `SourceReader` (contract) that fans out to per-source readers by `fmt`."""

    def __init__(self, sources: dict[str, dict] = SOURCES):
        self.sources = sources
        sqlite_sources = {s: cfg for s, cfg in sources.items() if cfg["fmt"] == "sqlite"}
        self._sqlite_reader = SqliteSourceReader(
            paths={s: cfg["db"] for s, cfg in sqlite_sources.items()},
            key_cols={s: cfg["key_field"] for s, cfg in sqlite_sources.items()},
            tables={s: cfg["table"] for s, cfg in sqlite_sources.items()},
        ) if sqlite_sources else None
        self._csv_reader = CsvSourceReader() if any(cfg["fmt"] == "csv" for cfg in sources.values()) else None

    def _reader_for(self, source: str):
        fmt = self.sources[source]["fmt"]
        if fmt == "sqlite":
            return self._sqlite_reader
        if fmt == "csv":
            return self._csv_reader
        raise ValueError(f"unknown source fmt {fmt!r} for source {source!r}")

    def list_fields(self, source: str) -> list[str]:
        return self._reader_for(source).list_fields(source)

    def sample_field(self, source: str, field: str, n: int = 3) -> list[str]:
        return self._reader_for(source).sample_field(source, field, n)

    def read_value(self, ref) -> str:
        source = source_of(ref.locator)
        return self._reader_for(source).read_value(ref)

    # ---- private, mirrors CsvSourceReader's non-protocol helpers ----
    def list_rows(self, source: str) -> list[dict]:
        return self._reader_for(source).list_rows(source)

    def key_field(self, source: str) -> str:
        return self.sources[source]["key_field"]
