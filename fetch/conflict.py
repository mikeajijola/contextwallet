"""Unit 4 — the conflict object. Conflicts are SURFACED, never silently resolved.

When one principal has several values for one ontology node (the multiplicity the store's
`cells_for_node` returns), we assemble a `ConflictSet` rather than pick one:

  agreed             — all values equal.
  conflict_ordered   — values differ AND every value is timestamped -> a default is offered
                       (the most recent), but every value is still shown.
  conflict_unordered — values differ AND at least one value lacks a timestamp -> NO default;
                       a human/agent must choose. Legacy systems often lack timestamps, so
                       this is a first-class outcome, not an error.
"""
from __future__ import annotations
from datetime import datetime
from typing import Optional

from contract import ConflictSet, ConflictValue


def parse_ts(raw: Optional[str]) -> Optional[datetime]:
    s = (raw or "").strip()
    if not s:
        return None
    try:
        return datetime.fromisoformat(s)
    except ValueError:
        for fmt in ("%Y-%m-%d", "%Y/%m/%d"):
            try:
                return datetime.strptime(s, fmt)
            except ValueError:
                continue
    return None


def build_conflict(principal_id: str, ontology_node: str,
                   values: list[ConflictValue]) -> ConflictSet:
    distinct = {v.value for v in values}

    if len(distinct) <= 1:
        return ConflictSet(principal_id=principal_id, ontology_node=ontology_node,
                           values=values, status="agreed",
                           default_selection=0 if values else None)

    if values and all(v.timestamp is not None for v in values):
        latest = max(range(len(values)), key=lambda i: values[i].timestamp)
        return ConflictSet(principal_id=principal_id, ontology_node=ontology_node,
                           values=values, status="conflict_ordered", default_selection=latest)

    return ConflictSet(principal_id=principal_id, ontology_node=ontology_node,
                       values=values, status="conflict_unordered", default_selection=None)
