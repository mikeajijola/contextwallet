"""Unit 1 — the reference inversion, plus the cheap deterministic seed grouping.

The single most important idea in the system: facts carry references and types,
NEVER values. `build_cells` turns classified source fields into placeholder `Cell`s
that locate their value at the source but hold no value. It reads ZERO values — it
must never call `reader.read_value`.

Identity is deliberately OUT of minting: a locator is `source:row_key:field` (see
locators.py), so a cell cannot betray whose value it is. Grouping is applied at write
time via `store.put_cell_for(principal_id, cell)`. `seed_grouping` produces a cheap,
deterministic mint-time grouping (exact email match only — no embeddings, no LLM) so
the store answers `cells_for(principal_id)` on day one; query-time resolution (Unit 3)
can override it live.
"""
from __future__ import annotations
import hashlib

from contract import Cell, Reference, SourceReader, TypeDescriptor
from locators import make_locator


def build_cells(
    source: str,
    row_key: str,
    classified_fields: list[tuple[str, TypeDescriptor]],
    reader: SourceReader,
    default_policy_id: str,
) -> list[Cell]:
    """Mint placeholder reference-cells for one source row's classified fields.

    For each `(field_name, TypeDescriptor)` a `Cell` is built whose `ref` locates the
    value at the source (`make_locator(source, row_key, field)`) and whose `type`
    carries the ontology node. `cell_id = sha256(locator)[:16]` — a stable, collision-
    safe hash of the canonical opaque locator, so re-onboarding the same field is
    idempotent. State is always ``placeholder``.

    The `reader` is part of the seam signature but is intentionally NOT read here — a
    spy reader whose `read_value` raises proves values are never touched at map-build
    time. Grouping (principal_id) is NOT set here; the caller applies it via
    `store.put_cell_for`.
    """
    cells: list[Cell] = []
    for field_name, type_desc in classified_fields:
        locator = make_locator(source, row_key, field_name)
        cells.append(
            Cell(
                cell_id=hashlib.sha256(locator.encode()).hexdigest()[:16],
                ref=Reference(source=source, locator=locator, resolver=source),
                type=type_desc,
                policy_id=default_policy_id,
                state="placeholder",
                materialised=None,
            )
        )
    return cells


def normalise_email(email: str) -> str:
    """Canonical form for exact-match grouping."""
    return email.strip().lower()


def seed_grouping(records: list[tuple[str, str, str]]) -> dict[tuple[str, str], str]:
    """Cheap deterministic mint-time grouping: two rows share a principal_id iff they
    share a normalised, non-empty email.

    `records` is a list of `(source, row_key, email)`. Returns `{(source, row_key):
    principal_id}` for rows that get grouped. Rows with a blank email are OMITTED
    (left ungrouped) — that is intentional: it leaves the interesting cross-source
    merge (e.g. Dana, whose second record has no email) for the live query-time
    resolver to solve under a capability. No embeddings, no LLM, instant.
    """
    by_email: dict[str, list[tuple[str, str]]] = {}
    for source, row_key, email in records:
        e = normalise_email(email)
        if not e:
            continue
        by_email.setdefault(e, []).append((source, row_key))

    groups: dict[tuple[str, str], str] = {}
    for e, members in by_email.items():
        principal_id = "grp_" + hashlib.sha256(e.encode()).hexdigest()[:12]
        for key in members:
            groups[key] = principal_id
    return groups
