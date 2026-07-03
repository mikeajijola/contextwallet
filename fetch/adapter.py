"""Unit 4 — the Adapter: the thing `Reference.resolver` names, that can dereference a ref.

Adapter is Unit-4-internal (not a frozen contract protocol). It wraps a `SourceReader` and
is the ONLY place a raw value read happens for the fetch ladder, so the ladder can count and
cache around it. `fetch_count` exists so tests can prove the cache prevents repeat reads.
"""
from __future__ import annotations

from contract import Reference, SourceReader, Value


class CsvAdapter:
    def __init__(self, reader: SourceReader) -> None:
        self.reader = reader
        self.fetch_count = 0

    def fetch(self, ref: Reference) -> Value:
        self.fetch_count += 1
        return self.reader.read_value(ref)
