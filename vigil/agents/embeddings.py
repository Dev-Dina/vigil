"""LOCAL embedding pipeline for vector retrieval (specs/rag.md § Retrieval stack).

Embeddings are computed LOCALLY — no embedding API, no OpenRouter. Two backends behind one
``Embedder`` protocol:

- ``HashingEmbedder`` (DEFAULT, hermetic): a deterministic, dependency-free, fully-offline
  hashing embedding. No model download, no network — so CI/tests embed for real and the same
  text always yields the same vector. This is what runs everywhere today.
- ``SentenceTransformerEmbedder`` (lazy, optional): the real semantic model
  (`bge-small-en-v1.5` / `all-MiniLM-L6-v2`, dim 384) for local/demo runs. ``sentence_transformers``
  is imported lazily INSIDE the class so it never touches the light/golden import paths (like
  torch). NOTE: it fetches model weights on first use (a network/cache step) → NOT hermetic;
  that is why the hashing backend is the default for CI.

Both produce ``EMBED_DIM``-length L2-normalised float vectors, so the DB column (`vector(384)`)
and any stored vectors are dimension-compatible across a future backend swap with NO migration.
"""

from __future__ import annotations

import hashlib
import math
from typing import Protocol, runtime_checkable

from vigil.core.config import get_settings

#: Vector dimension — matches all-MiniLM-L6-v2 / bge-small-en-v1.5 so the real ST model drops in
#: with no schema migration.
EMBED_DIM = 384


@runtime_checkable
class Embedder(Protocol):
    dim: int

    def embed(self, text: str) -> list[float]: ...

    def embed_batch(self, texts: list[str]) -> list[list[float]]: ...


def _l2_normalise(vec: list[float]) -> list[float]:
    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0.0:
        return vec
    return [v / norm for v in vec]


class HashingEmbedder:
    """Deterministic, offline hashing embedding (the hermetic default).

    Token → stable bucket via blake2b; bucket counts form the vector, then L2-normalised so
    cosine distance is meaningful. No randomness, no network, no model file. Same text → same
    vector on every machine and run.
    """

    def __init__(self, dim: int = EMBED_DIM) -> None:
        self.dim = dim

    def _tokens(self, text: str) -> list[str]:
        return [t for t in text.lower().split() if t]

    def embed(self, text: str) -> list[float]:
        vec = [0.0] * self.dim
        for tok in self._tokens(text):
            h = hashlib.blake2b(tok.encode("utf-8"), digest_size=8).digest()
            bucket = int.from_bytes(h, "big") % self.dim
            # sign bit from the next hash byte keeps it from being all-positive.
            sign = 1.0 if (h[0] & 1) == 0 else -1.0
            vec[bucket] += sign
        return _l2_normalise(vec)

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        return [self.embed(t) for t in texts]


class SentenceTransformerEmbedder:
    """Real semantic embedder (lazy ``sentence_transformers`` import; local/demo only).

    Imported lazily so this module stays out of the light/golden import path. Requires the model
    weights (fetched/cached on first use) → not hermetic; selected only when configured.
    """

    def __init__(self, model_name: str, dim: int = EMBED_DIM) -> None:
        from sentence_transformers import SentenceTransformer  # deferred, optional dep

        self._model = SentenceTransformer(model_name)
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        vecs = self._model.encode(texts, normalize_embeddings=True)
        return [list(map(float, v)) for v in vecs]


def get_embedder() -> Embedder:
    """Return the active embedder: hermetic ``hashing`` default, or ``sentence_transformers``.

    Explicit (not a silent fallback): ``VIGIL_EMBEDDING_BACKEND=sentence_transformers`` selects
    the real model on local/demo; the default ``hashing`` keeps CI/tests offline + deterministic.
    """
    settings = get_settings()
    if settings.embedding_backend == "sentence_transformers":
        return SentenceTransformerEmbedder(
            settings.embedding_model, dim=settings.embedding_dim
        )
    return HashingEmbedder(dim=settings.embedding_dim)
