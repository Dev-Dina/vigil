"""Hermetic posture for the Guide tests: stub the Guide LLM (no live call, no key).

Mirrors the app's VIGIL_LLM_STUB posture but for the Guide's OWN client. Set before any
guide.config import so the cached config picks it up.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

os.environ.setdefault("VIGIL_GUIDE_LLM_STUB", "true")


@pytest.fixture(scope="session")
def guide_index():  # type: ignore[no-untyped-def]
    """Build the Guide's file-backed index from the approved docs once (real offline embedder)."""
    from guide.embeddings import get_embedder
    from guide.retrieval import build_index, load_index

    docs = Path(__file__).resolve().parents[2] / "guide" / "approved_docs"
    out = Path(__file__).resolve().parent / "_built_index.json"
    n = build_index(docs, out, get_embedder())
    assert n > 0, "expected chunks from the approved docs"
    idx = load_index(out)
    yield idx
    out.unlink(missing_ok=True)
