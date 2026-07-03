"""Determinism guard for the shared embedder (the dedupe of onboarding/embed.py +
resolution/embed.py into one root `embed.py`).

The whole point of the dedupe: classification (Unit 2) and entity-resolution (Unit 3) now embed
in the SAME space, so a model bump can never silently move one and not the other. These tests
prove there is one embedding space, not two that happen to agree — the test that would have
caught the divergence the dedupe prevents.
"""
from __future__ import annotations

import numpy as np

import embed
# the symbols each unit actually imports and uses for embedding:
from onboarding.classify import default_embedder as u2_default_embedder   # Unit 2 path
from resolution.resolver import default_embedder as u3_default_embedder   # Unit 3 path


def test_both_units_resolve_the_same_shared_embedder():
    # Unit 2 and Unit 3 import the exact same function object from the shared foundation...
    assert u2_default_embedder is u3_default_embedder is embed.default_embedder
    # ...and it hands back the same process-wide singleton instance to both.
    assert u2_default_embedder() is u3_default_embedder() is embed.default_embedder()


def test_same_string_embeds_identically_through_both_unit_paths():
    """Embed the same string via the Unit 2 path and the Unit 3 path — vectors are IDENTICAL,
    proving a single embedding space (not two that coincidentally match)."""
    s = "Colin Marsh | Stripe"
    v2 = u2_default_embedder().embed([s])
    v3 = u3_default_embedder().embed([s])
    assert np.array_equal(v2, v3)                 # bit-for-bit identical, same shared embedder


def test_embedding_is_deterministic_across_reembeds():
    """Fixed model -> deterministic: re-embedding the same input yields the same vector. The
    seed-based demo determinism relies on this."""
    e = embed.default_embedder()
    s = "Dana Osei | Acme"
    assert np.array_equal(e.embed([s]), e.embed([s]))


def test_single_model_pin_exists_in_exactly_one_place():
    """Success criterion of the dedupe: `MODEL_NAME` is pinned in exactly ONE file (root
    embed.py), so there is one model constant in the whole codebase."""
    from pathlib import Path
    root = Path(__file__).resolve().parent.parent
    files = []
    for py in root.rglob("*.py"):
        if ".venv" in py.parts:
            continue
        for line in py.read_text().splitlines():
            if line.strip().startswith("MODEL_NAME ="):   # the assignment, not incidental mentions
                files.append(str(py.relative_to(root)))
    assert files == ["embed.py"], f"MODEL_NAME must be pinned in exactly one file, found: {files}"
