"""One-time, network-ON step: vendor BAAI/bge-small-en-v1.5 into `embed.MODEL_DIR`.

Run this once, with network access, before running the app or its tests offline:

    python scripts/fetch_model.py

This does NOT change the model, the vectors, or any threshold — it is the exact same
`TextEmbedding(model_name=MODEL_NAME, cache_dir=...)` call `embed.py` makes at runtime, just
with network allowed so it can populate the cache instead of requiring it to already be
there. After this, `embed.py` forces `HF_HUB_OFFLINE=1` and loads the same snapshot with zero
network calls — same call, same weights, same vectors, network on to fetch once, off forever
after.
"""
from __future__ import annotations
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from embed import MODEL_NAME, MODEL_DIR  # noqa: E402  (path insert must run first)


def main() -> None:
    MODEL_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Fetching {MODEL_NAME} into {MODEL_DIR} (network required for this step only)...")

    from fastembed import TextEmbedding

    TextEmbedding(model_name=MODEL_NAME, cache_dir=str(MODEL_DIR))

    print("Done. Set HF_HUB_OFFLINE=1 (embed.py does this itself) and the app runs fully offline.")


if __name__ == "__main__":
    main()
