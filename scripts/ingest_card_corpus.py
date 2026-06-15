#!/usr/bin/env python3
"""Embed the global card corpus into ``document_chunk`` (specs/rag.md § Retrieval stack).

Runs the local embedder over the model cards + PHASE3_CARD and writes global (null-sponsor)
rows under a PLATFORM session. Hermetic with the default hashing embedder (no network); set
VIGIL_EMBEDDING_BACKEND=sentence_transformers for the real model on local/demo.

Usage: uv run python scripts/ingest_card_corpus.py
"""

from __future__ import annotations

import sys

from vigil.agents.corpus import ingest_card_corpus
from vigil.agents.embeddings import get_embedder
from vigil.repositories.session import platform_session


def main() -> int:
    embedder = get_embedder()
    with platform_session() as session:
        n = ingest_card_corpus(session, embedder)
    print(f"ingested {n} global card-corpus chunks (dim={embedder.dim})")
    return 0


if __name__ == "__main__":
    sys.exit(main())
