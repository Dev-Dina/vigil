#!/usr/bin/env python3
"""Vendor the embedding model into the repo (one-time / re-vendor; requires network).

Downloads sentence-transformers/all-MiniLM-L6-v2 and saves the self-contained model directory to
``data/models/embeddings/all-MiniLM-L6-v2`` so the runtime embedder loads it OFFLINE (the
embedder NEVER downloads at runtime — see vigil/agents/embeddings.py). Run this only to (re)create
the vendored weights; CI/tests use the committed copy and never call this.

Usage: uv run python scripts/vendor_embedding_model.py
"""

from __future__ import annotations

import sys
from pathlib import Path

_MODEL_ID = "sentence-transformers/all-MiniLM-L6-v2"
_DEST = Path("data/models/embeddings/all-MiniLM-L6-v2")


def main() -> int:
    from sentence_transformers import SentenceTransformer

    _DEST.parent.mkdir(parents=True, exist_ok=True)
    model = SentenceTransformer(_MODEL_ID)
    model.save(str(_DEST))
    has_weights = (_DEST / "config.json").exists()
    print(f"vendored {_MODEL_ID} -> {_DEST} (config present: {has_weights})")
    return 0 if has_weights else 1


if __name__ == "__main__":
    sys.exit(main())
