"""Card-corpus ingestion for the Phase-5 doc-grounding corpus (specs/rag.md § Doc-grounding).

The Phase-5 vector corpus = the model cards + PHASE3_CARD — GLOBAL project docs (decision (a),
written with ``sponsor_id=None``). This module locates the card files, chunks them, embeds them
LOCALLY (vigil.agents.embeddings), and writes global ``document_chunk`` rows. Deterministic:
same files + same embedder → same chunks/vectors. Idempotent per source: re-ingesting a file
replaces its prior chunks.

Sponsor-scoped protocol/SOP docs are DEFERRED (not Phase 5); this writes globals only.
"""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import delete
from sqlalchemy.orm import Session

from vigil.agents.embeddings import Embedder
from vigil.db.models import DocumentChunk
from vigil.repositories import documents as doc_repo

#: Repo-root-relative corpus globs: every model card + the Phase-3 scorecard.
_CORPUS_GLOBS = ("data/models/**/*model_card*.md", "data/models/PHASE3_CARD.md")

# Chunking: paragraph-ish blocks, bounded so a chunk embeds meaningfully but stays citable.
_MAX_CHARS = 800


def _project_root() -> Path:
    # vigil/agents/corpus.py -> repo root is three parents up.
    return Path(__file__).resolve().parents[2]


def card_corpus_files(root: Path | None = None) -> list[Path]:
    base = root or _project_root()
    seen: dict[str, Path] = {}
    for pattern in _CORPUS_GLOBS:
        for p in base.glob(pattern):
            if p.is_file():
                seen[str(p.resolve())] = p
    return sorted(seen.values(), key=lambda p: str(p))


def chunk_text(text: str, *, max_chars: int = _MAX_CHARS) -> list[str]:
    """Split on blank lines into paragraph blocks, packing up to ``max_chars`` per chunk."""
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    buf = ""
    for para in paras:
        if buf and len(buf) + len(para) + 2 > max_chars:
            chunks.append(buf)
            buf = para
        else:
            buf = f"{buf}\n\n{para}" if buf else para
    if buf:
        chunks.append(buf)
    return chunks


def ingest_card_corpus(
    session: Session, embedder: Embedder, *, root: Path | None = None
) -> int:
    """Embed + write the global card corpus as ``document_chunk`` rows. Returns rows written.

    Must run under a PLATFORM session (global/null-sponsor rows; the policy's WITH CHECK allows
    null-sponsor writes only for platform). Idempotent per source_ref.
    """
    base = root or _project_root()
    written = 0
    for path in card_corpus_files(base):
        source_ref = str(path.relative_to(base)).replace("\\", "/")
        text = path.read_text(encoding="utf-8")
        chunks = chunk_text(text)
        # Idempotent: clear any prior global chunks for this source before re-inserting.
        session.execute(
            delete(DocumentChunk).where(
                DocumentChunk.source_ref == source_ref,
                DocumentChunk.sponsor_id.is_(None),
            )
        )
        embeddings = embedder.embed_batch(chunks)
        for i, (chunk, emb) in enumerate(zip(chunks, embeddings, strict=True)):
            doc_repo.add_chunk(
                session,
                source_ref=source_ref,
                chunk_index=i,
                chunk_text=chunk,
                embedding=emb,
                sponsor_id=None,
            )
            written += 1
    return written
