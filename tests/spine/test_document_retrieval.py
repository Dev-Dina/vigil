"""Gate 5.3 — pgvector document_chunk: scoped retrieval + the sacred cross-tenant boundary.

Corpus decision (a): global (null-sponsor) card docs are retrievable by everyone; sponsor-scoped
rows follow the strict participant_score pattern. This proves the strict-tenant path NOW (so the
deferred protocol/SOP docs are RLS-safe by construction) AND that globals are shared.

Real Postgres via migrated_db (RLS + pgvector are DB features).
"""

from __future__ import annotations

import uuid

from vigil.agents.corpus import ingest_card_corpus
from vigil.agents.embeddings import HashingEmbedder
from vigil.repositories import documents as doc_repo
from vigil.repositories.session import platform_session, sponsor_bootstrap_session

_EMB = HashingEmbedder()


def _add_sponsor_chunk(sponsor_id: str, text: str) -> str:
    with sponsor_bootstrap_session(sponsor_id) as session:
        row = doc_repo.add_chunk(
            session,
            source_ref="protocol/secret.md",
            chunk_index=0,
            chunk_text=text,
            embedding=_EMB.embed(text),
            sponsor_id=uuid.UUID(sponsor_id),
        )
        return str(row.id)


def _add_global_chunk(text: str) -> str:
    with platform_session() as session:
        row = doc_repo.add_chunk(
            session,
            source_ref="data/models/PHASE3_CARD.md",
            chunk_index=999,
            chunk_text=text,
            embedding=_EMB.embed(text),
            sponsor_id=None,
        )
        return str(row.id)


# ---------------------------------------------------------------------------
# 1. Cross-tenant (sacred): a sponsor-scoped chunk never leaks to another sponsor
# ---------------------------------------------------------------------------


def test_sponsor_scoped_chunk_not_visible_cross_tenant(
    migrated_db: dict[str, str],
) -> None:
    ids = migrated_db
    secret_text = "Sponsor A confidential protocol amendment alpha bravo charlie"
    id_a = _add_sponsor_chunk(ids["sponsor_a"], secret_text)
    global_text = "Vigil sequence model PR-AUC method demonstration scorecard"
    id_global = _add_global_chunk(global_text)

    q = _EMB.embed(secret_text)

    # Sponsor B searches for A's exact text — must NOT get A's sponsor-scoped chunk.
    with sponsor_bootstrap_session(ids["sponsor_b"]) as session:
        hits_b = {
            h.id for h in doc_repo.search_chunks(session, query_embedding=q, k=20)
        }
    assert id_a not in hits_b, (
        "cross-tenant leak: Sponsor B retrieved Sponsor A's chunk"
    )
    assert id_global in hits_b, "global card chunk should be retrievable by Sponsor B"

    # Sponsor A sees its own chunk + the global one.
    with sponsor_bootstrap_session(ids["sponsor_a"]) as session:
        hits_a = {
            h.id for h in doc_repo.search_chunks(session, query_embedding=q, k=20)
        }
    assert id_a in hits_a, "Sponsor A could not retrieve its own chunk"
    assert id_global in hits_a, "global card chunk should be retrievable by Sponsor A"


# ---------------------------------------------------------------------------
# 2. Retrieval returns citable card chunks (the doc-grounding path)
# ---------------------------------------------------------------------------


def test_card_corpus_retrieval_returns_citable_chunks(
    migrated_db: dict[str, str],
) -> None:
    with platform_session() as session:
        n = ingest_card_corpus(session, _EMB)
    assert n > 0, "card corpus ingested no chunks"

    # A metric-style query retrieves top-k chunks each carrying a source_ref to cite.
    q = _EMB.embed("sequence model PR-AUC lift over the structural baseline")
    with sponsor_bootstrap_session(migrated_db["sponsor_a"]) as session:
        hits = doc_repo.search_chunks(session, query_embedding=q, k=5)
    assert hits, "no chunks retrieved from the card corpus"
    assert all(h.source_ref for h in hits), (
        "every retrieved chunk must carry a source_ref"
    )
    assert any(h.source_ref.endswith(".md") for h in hits)
    # Distances are ordered ascending (closest first).
    dists = [h.distance for h in hits]
    assert dists == sorted(dists)


# ---------------------------------------------------------------------------
# 3. Extension present
# ---------------------------------------------------------------------------


def test_vector_extension_enabled(migrated_db: dict[str, str]) -> None:
    from sqlalchemy import text

    with platform_session() as session:
        present = session.execute(
            text("SELECT 1 FROM pg_extension WHERE extname = 'vector'")
        ).scalar()
    assert present == 1, "pgvector extension is not enabled"
