"""Gate 5.3 — local embedding pipeline (pure-unit, hermetic, no DB, no network)."""

from __future__ import annotations

import math

from vigil.agents.embeddings import EMBED_DIM, HashingEmbedder, get_embedder


def test_hashing_embedder_deterministic_and_dim() -> None:
    e = HashingEmbedder()
    v1 = e.embed("why is participant flagged high risk")
    v2 = e.embed("why is participant flagged high risk")
    assert v1 == v2, "embedding must be deterministic (same text → same vector)"
    assert len(v1) == EMBED_DIM == 384


def test_hashing_embedder_l2_normalised() -> None:
    v = HashingEmbedder().embed("pr-auc lift calibration")
    norm = math.sqrt(sum(x * x for x in v))
    assert abs(norm - 1.0) < 1e-9 or norm == 0.0


def test_hashing_embedder_distinguishes_text() -> None:
    e = HashingEmbedder()
    a = e.embed("champion model risk score")
    b = e.embed("completely unrelated tokens xyzzy plover")
    assert a != b, "different text should not collide to the same vector"


def test_embed_batch_matches_single() -> None:
    e = HashingEmbedder()
    texts = ["alpha beta", "gamma delta"]
    batch = e.embed_batch(texts)
    assert batch == [e.embed(t) for t in texts]


def test_get_embedder_defaults_to_hashing() -> None:
    # Default backend is the hermetic hashing embedder (no network, no model download).
    assert isinstance(get_embedder(), HashingEmbedder)
