"""Document-chunk repository — the only DB path for the RAG vector corpus.

Reads/writes run under the caller's RLS-scoped session; the ``document_chunk_scope`` policy
(global-or-sponsor, decision (a)) decides visibility — a sponsor session sees global docs + its
own, NEVER another sponsor's. This module NEVER hand-rolls a sponsor filter as the guarantee;
RLS is the hard boundary. Retrieval returns the chunk + its ``source_ref`` so the agent can cite.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from vigil.db.models import DocumentChunk


@dataclass(frozen=True, slots=True)
class RetrievedChunk:
    id: str
    source_ref: str
    chunk_index: int
    chunk_text: str
    distance: float  # cosine distance (lower = closer)
    sponsor_id: str | None


def add_chunk(
    session: Session,
    *,
    source_ref: str,
    chunk_index: int,
    chunk_text: str,
    embedding: list[float],
    sponsor_id: uuid.UUID | None,
) -> DocumentChunk:
    """Insert one embedded chunk. ``sponsor_id=None`` = a GLOBAL project doc (cards).

    Runs under the caller's RLS session; the policy's WITH CHECK enforces that a sponsor session
    may write only its own ``sponsor_id`` rows, and only a platform session may write global
    (null-sponsor) rows.
    """
    row = DocumentChunk(
        sponsor_id=sponsor_id,
        source_ref=source_ref,
        chunk_index=chunk_index,
        chunk_text=chunk_text,
        embedding=embedding,
    )
    session.add(row)
    session.flush()
    return row


def search_chunks(
    session: Session, *, query_embedding: list[float], k: int = 5
) -> list[RetrievedChunk]:
    """Top-k cosine-nearest chunks visible to the caller's scope (RLS-filtered).

    The session's RLS policy restricts the candidate set to global + in-scope rows BEFORE
    ranking, so a cross-tenant chunk can never be returned. Returns chunks with ``source_ref``
    for citation, ordered by ascending cosine distance.
    """
    distance = DocumentChunk.embedding.cosine_distance(query_embedding)
    rows = session.execute(
        select(DocumentChunk, distance.label("distance"))
        .order_by(distance.asc())
        .limit(k)
    ).all()
    return [
        RetrievedChunk(
            id=str(chunk.id),
            source_ref=chunk.source_ref,
            chunk_index=chunk.chunk_index,
            chunk_text=chunk.chunk_text,
            distance=float(dist),
            sponsor_id=str(chunk.sponsor_id) if chunk.sponsor_id is not None else None,
        )
        for chunk, dist in rows
    ]
