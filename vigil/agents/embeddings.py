"""LOCAL embedding pipeline for vector retrieval (specs/rag.md § Retrieval stack).

ONE real embedder, used EVERYWHERE (CI, tests, demo): ``SentenceTransformerEmbedder`` over the
**vendored** all-MiniLM-L6-v2 weights (dim 384). Decision (B): drop the lexical hashing default —
retrieval is semantic by construction, with NO lexical fallback that could be silently selected.

HERMETIC BY CONSTRUCTION — NO RUNTIME NETWORK:
- The model weights live IN-REPO under ``data/models/embeddings/all-MiniLM-L6-v2`` (tracked, like
  the ``.pt``/``.pkl`` model artifacts). The embedder loads from that LOCAL DIRECTORY PATH, never
  a hub id.
- ``HF_HUB_OFFLINE`` / ``TRANSFORMERS_OFFLINE`` are forced on before the model loads, so even the
  transformers/hub machinery makes no HEAD/download call. A test run with no network still
  produces real ST embeddings (proven in tests by blocking sockets during load+embed).

``sentence_transformers`` (and torch) are imported LAZILY inside the class so this module — and
the light/golden/check_specs import paths — do not drag the heavy stack in.

Determinism: the model runs in eval mode on CPU with L2-normalised output; the same text yields
the same vector run-to-run (asserted within ``atol=1e-6`` to guard against minor float drift).
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Protocol, runtime_checkable

from vigil.core.config import get_settings

#: Embedding dimension — all-MiniLM-L6-v2 is 384 (matches the document_chunk vector(384) column).
EMBED_DIM = 384


@runtime_checkable
class Embedder(Protocol):
    dim: int

    def embed(self, text: str) -> list[float]: ...

    def embed_batch(self, texts: list[str]) -> list[list[float]]: ...


def _project_root() -> Path:
    # vigil/agents/embeddings.py -> repo root is three parents up.
    return Path(__file__).resolve().parents[2]


def resolve_model_path(path: str | None = None) -> Path:
    """Resolve the vendored-model path; relative paths bind to the repo root (not cwd)."""
    raw = Path(path or get_settings().embedding_model_path)
    return raw if raw.is_absolute() else _project_root() / raw


class SentenceTransformerEmbedder:
    """The single real embedder — vendored all-MiniLM-L6-v2, loaded OFFLINE (no network).

    Forces offline mode and loads from a LOCAL directory, so neither load nor encode reaches the
    network. ``sentence_transformers`` is imported lazily (heavy: torch + transformers).
    """

    def __init__(
        self, model_path: str | Path | None = None, dim: int = EMBED_DIM
    ) -> None:
        # Force offline BEFORE the transformers/hub machinery is imported/used.
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

        resolved = (
            Path(model_path)
            if model_path is not None and Path(model_path).is_absolute()
            else resolve_model_path(str(model_path) if model_path is not None else None)
        )
        if not (resolved / "config.json").exists():
            raise FileNotFoundError(
                f"vendored embedding model not found at {resolved} — the all-MiniLM-L6-v2 "
                "weights must be present in-repo (run scripts/vendor_embedding_model.py). "
                "The embedder NEVER downloads at runtime (hermetic)."
            )

        from sentence_transformers import (
            SentenceTransformer,
        )  # deferred — heavy (torch)

        self._model = SentenceTransformer(str(resolved), device="cpu")
        self._model.eval()
        self.dim = dim

    def embed(self, text: str) -> list[float]:
        return self.embed_batch([text])[0]

    def embed_batch(self, texts: list[str]) -> list[list[float]]:
        vecs = self._model.encode(
            texts,
            normalize_embeddings=True,
            convert_to_numpy=True,
            show_progress_bar=False,
        )
        return [list(map(float, v)) for v in vecs]


@lru_cache(maxsize=1)
def get_embedder() -> Embedder:
    """Return the process-wide embedder (loaded once). Always the real, offline ST embedder.

    No backend switch and no lexical fallback — retrieval is semantic by construction; there is
    no way to silently default to a non-semantic embedder.
    """
    settings = get_settings()
    return SentenceTransformerEmbedder(
        settings.embedding_model_path, dim=settings.embedding_dim
    )
