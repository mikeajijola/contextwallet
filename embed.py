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
import os
from pathlib import Path
from typing import Protocol

import numpy as np

MODEL_NAME = "BAAI/bge-small-en-v1.5"   # the ONE model pin — one constant, one place.

# Repo-local vendored snapshot. `scripts/fetch_model.py` populates this ONCE with network on;
# every load after that is offline and byte-identical (same model_name, same cache_dir).
MODEL_DIR = Path(__file__).resolve().parent / "models"


class Embedder(Protocol):
    def embed(self, texts: list[str]) -> np.ndarray:  # (n, dim), rows L2-normalised
        ...


def _load_offline_model():
    """Load MODEL_NAME strictly from the vendored MODEL_DIR snapshot — never the network.

    Fails loudly (not a silent network fallback) if the snapshot hasn't been vendored yet.
    Forces HF_HUB_OFFLINE, the switch fastembed/huggingface_hub's own downloader checks
    before it will even consider a network call (verified against fastembed 0.8.0's
    `ModelManagement.download_model`); FASTEMBED_CACHE_PATH/HF_HOME are set too as a belt-
    and-braces fallback in case a code path resolves the cache dir from env instead of the
    `cache_dir=` kwarg. None of this touches MODEL_NAME, so the vectors are identical to an
    online load of the same model.
    """
    if not MODEL_DIR.is_dir() or not any(MODEL_DIR.iterdir()):
        raise RuntimeError(
            f"{MODEL_NAME} is not vendored at {MODEL_DIR}. Run `python scripts/fetch_model.py` "
            "once, with network access, before running offline."
        )
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ.setdefault("FASTEMBED_CACHE_PATH", str(MODEL_DIR))
    os.environ.setdefault("HF_HOME", str(MODEL_DIR))
    from fastembed import TextEmbedding
    return TextEmbedding(model_name=MODEL_NAME, cache_dir=str(MODEL_DIR))


class FastEmbedEmbedder:
    """Real embedder. First call loads the vendored model (no network; see `_load_offline_model`)."""

    _model = None  # process-wide singleton across instances

    def embed(self, texts: list[str]) -> np.ndarray:
        if FastEmbedEmbedder._model is None:
            FastEmbedEmbedder._model = _load_offline_model()
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
