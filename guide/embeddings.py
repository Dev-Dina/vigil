"""The Guide's OWN offline embedder (specs/isolation.md § owned code; rag.md § Retrieval stack).

A Guide-owned copy of the app's offline embedder approach — same vendored all-MiniLM-L6-v2 weights
(dim 384), loaded from a LOCAL directory the Guide MOUNTS, with HF offline forced so neither load
nor encode touches the network (hermetic). Imports nothing from `vigil.*`; reads the weights path
from the Guide's own config. ``sentence_transformers`` (and torch) are lazy-imported so the light
import paths stay heavy-stack-free.
"""

from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path

from guide.config import get_config

EMBED_DIM = 384


def _repo_root() -> Path:
    # guide/embeddings.py -> repo root is two parents up.
    return Path(__file__).resolve().parents[1]


def _resolve_model_path(path: str) -> Path:
    raw = Path(path)
    return raw if raw.is_absolute() else _repo_root() / raw


class GuideEmbedder:
    """Vendored all-MiniLM-L6-v2, loaded OFFLINE (no network) — the Guide's only embedder."""

    def __init__(self, model_path: str | None = None, dim: int = EMBED_DIM) -> None:
        os.environ.setdefault("HF_HUB_OFFLINE", "1")
        os.environ.setdefault("TRANSFORMERS_OFFLINE", "1")
        os.environ.setdefault("HF_HUB_DISABLE_TELEMETRY", "1")

        resolved = _resolve_model_path(model_path or get_config().embedding_model_path)
        if not (resolved / "config.json").exists():
            raise FileNotFoundError(
                f"Guide embedding model not found at {resolved} — the all-MiniLM-L6-v2 weights "
                "must be mounted/bundled for the Guide. The embedder NEVER downloads at runtime."
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
def get_embedder() -> GuideEmbedder:
    """Return the Guide's process-wide offline embedder (loaded once)."""
    cfg = get_config()
    return GuideEmbedder(cfg.embedding_model_path, dim=cfg.embedding_dim)
