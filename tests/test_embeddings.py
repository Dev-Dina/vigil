"""Gate 5.3-fix — the single real embedder is sentence-transformers, vendored + OFFLINE.

Proves: real ST embeddings (not lexical), produced with NO network (sockets blocked during a
fresh load + embed), deterministic, correct dim. Heavier than a pure-unit test (loads the model)
but hermetic — the vendored weights are in-repo.
"""

from __future__ import annotations

import socket

from vigil.agents.embeddings import (
    EMBED_DIM,
    SentenceTransformerEmbedder,
    get_embedder,
    resolve_model_path,
)


def test_vendored_model_present() -> None:
    path = resolve_model_path()
    assert (path / "config.json").exists(), f"vendored model missing at {path}"
    assert (path / "model.safetensors").exists()


def test_real_st_embeds_with_no_network(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # Block ALL socket creation, then load the model fresh + embed. If either step needed the
    # network this raises — proving load+embed are offline-by-construction.
    def _no_network(*args, **kwargs):  # noqa: ANN002, ANN003, ANN202
        raise AssertionError(
            "network access attempted during embedding (must be offline)"
        )

    monkeypatch.setattr(socket, "socket", _no_network)
    emb = SentenceTransformerEmbedder()
    vec = emb.embed("sequence model PR-AUC lift over the structural baseline")
    assert len(vec) == EMBED_DIM == 384
    assert any(v != 0.0 for v in vec)


def test_embeddings_deterministic() -> None:
    e = get_embedder()
    a = e.embed("why is participant flagged high risk")
    b = e.embed("why is participant flagged high risk")
    assert len(a) == len(b) == EMBED_DIM
    # CPU eval-mode inference is run-to-run stable; allow a tight tolerance for float drift.
    assert all(abs(x - y) < 1e-6 for x, y in zip(a, b, strict=True))


def test_embeddings_are_semantic_not_lexical() -> None:
    # A semantically-related pair should be closer than an unrelated pair, even with low lexical
    # overlap — something a hashing/lexical embedder could not guarantee.
    e = get_embedder()

    def cos(u: list[float], v: list[float]) -> float:
        return sum(
            a * b for a, b in zip(u, v, strict=True)
        )  # vectors are L2-normalised

    anchor = e.embed("the trial participant dropped out of the study")
    related = e.embed("the subject withdrew from the clinical trial")
    unrelated = e.embed("quarterly financial revenue projections for the fiscal year")
    assert cos(anchor, related) > cos(anchor, unrelated)
