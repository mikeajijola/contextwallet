"""Shared foundation — local, deterministic embeddings (the ONE embedder).

Wraps fastembed's `BAAI/bge-small-en-v1.5` (no API call, deterministic across runs). The model
is loaded lazily and once (process-wide singleton), because construction downloads/loads weights
and is the expensive part. `Embedder` is an interface other modules depend on, so tests can inject
a fake with controlled vectors.

This is the single canonical embedder for the whole system. It was previously duplicated as
`onboarding/embed.py` (Unit 2, classification shortlists) and `resolution/embed.py` (Unit 3,
entity-resolution candidates); both pinned the same model, so behaviour agreed only by
coincidence. Promoting it here means there is exactly ONE `MODEL_NAME` pin in the codebase, so
classification and resolution can never silently drift into different embedding spaces.

Leaf utility: imports only fastembed/numpy (+ stdlib). It must NOT import any unit, nor `contract`.
"""
from __future__ import annotations
from typing import Protocol

import numpy as np

MODEL_NAME = "BAAI/bge-small-en-v1.5"   # the ONE model pin — one constant, one place.


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> np.ndarray:  # (n, dim), rows L2-normalised
        ...


class FastEmbedEmbedder:
    """Real embedder. First call constructs the model (may download weights once)."""

    _model = None  # process-wide singleton across instances

    def embed(self, texts: list[str]) -> np.ndarray:
        if FastEmbedEmbedder._model is None:
            from fastembed import TextEmbedding
            FastEmbedEmbedder._model = TextEmbedding(model_name=MODEL_NAME)
        vecs = np.array(list(FastEmbedEmbedder._model.embed(list(texts))), dtype=np.float32)
        # bge vectors are already ~unit-norm, but normalise to make cosine a plain dot.
        norms = np.linalg.norm(vecs, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return vecs / norms


def cosine(a: np.ndarray, b: np.ndarray) -> float:
    """Cosine similarity between two 1-D vectors (assumes finite, non-zero)."""
    denom = float(np.linalg.norm(a) * np.linalg.norm(b))
    if denom == 0.0:
        return 0.0
    return float(np.dot(a, b) / denom)


_default: Embedder | None = None


def default_embedder() -> Embedder:
    """Shared process-wide real embedder (built on first use)."""
    global _default
    if _default is None:
        _default = FastEmbedEmbedder()
    return _default
