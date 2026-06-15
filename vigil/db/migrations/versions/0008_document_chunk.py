"""pgvector extension + document_chunk table — RAG vector corpus (specs/rag.md § Retrieval stack).

Enables the ``vector`` extension (the CI image is pgvector/pg16 — available, just not enabled)
and creates the embedded-chunk table for hybrid-RAG vector retrieval.

CORPUS-SCOPE DECISION (a) — GLOBAL-OR-SPONSOR, RLS-ready (flagged for ratification):
``sponsor_id`` is NULLABLE. NULL = a GLOBAL project doc (the model cards + PHASE3_CARD, the
Phase-5 doc-grounding corpus) readable by every caller. A non-null row is sponsor-scoped (the
DEFERRED protocol/SOP docs) and is fail-closed like ``participant_score``. One policy serves both
so adding sponsor-scoped docs later needs NO RLS re-architecture:
  - READ  (USING):      sponsor_id IS NULL  OR is_platform OR sponsor_id = current_sponsor
                        → globals visible to all; a sponsor sees its own; platform sees all;
                          another sponsor's rows are HIDDEN (fail-closed).
  - WRITE (WITH CHECK):  is_platform OR sponsor_id = current_sponsor
                        → only platform may write global (null) docs (the card ingest); a sponsor
                          may write only its OWN sponsor_id rows — never global, never cross-tenant.
FORCE RLS so the policy binds even for the table owner. Bespoke policy → NOT in TENANT_TABLES.

embedding is vector(384) (matches the local embedder dim; backend-swappable, no migration). An
hnsw cosine index serves top-k similarity for the small corpus (no training step, unlike ivfflat).

Applies cleanly on a fresh DB and on the existing 0001–0007 chain.

Revision ID: 0008_document_chunk
Revises: 0007_message_events
Create Date: 2026-06-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects import postgresql as pg

revision = "0008_document_chunk"
down_revision = "0007_message_events"
branch_labels = None
depends_on = None

_EMBED_DIM = 384


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "vector"')

    op.create_table(
        "document_chunk",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        # NULL = global project doc (cards); set = sponsor-scoped (deferred protocol/SOP docs).
        sa.Column(
            "sponsor_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("sponsor.id", ondelete="CASCADE"),
            nullable=True,
        ),
        sa.Column("source_ref", sa.String(512), nullable=False),
        sa.Column("chunk_index", sa.Integer, nullable=False),
        sa.Column("chunk_text", sa.Text, nullable=False),
        sa.Column("embedding", Vector(_EMBED_DIM), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_docchunk_source", "document_chunk", ["source_ref", "chunk_index"]
    )
    op.create_index("ix_docchunk_sponsor", "document_chunk", ["sponsor_id"])
    # hnsw cosine index — no training step, fine for the small grounding corpus.
    op.execute(
        "CREATE INDEX ix_docchunk_embedding ON document_chunk "
        "USING hnsw (embedding vector_cosine_ops)"
    )

    # RLS: global-or-sponsor (decision (a)). See module docstring for the read/write split.
    sponsor_guc = "NULLIF(current_setting('app.current_sponsor', true), '')"
    is_platform = "current_setting('app.is_platform', true) = 'on'"
    op.execute("ALTER TABLE document_chunk ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE document_chunk FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY document_chunk_scope ON document_chunk
        USING (sponsor_id IS NULL OR {is_platform} OR sponsor_id = {sponsor_guc}::uuid)
        WITH CHECK ({is_platform} OR sponsor_id = {sponsor_guc}::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS document_chunk_scope ON document_chunk")
    op.execute("DROP INDEX IF EXISTS ix_docchunk_embedding")
    op.drop_index("ix_docchunk_sponsor", table_name="document_chunk")
    op.drop_index("ix_docchunk_source", table_name="document_chunk")
    op.drop_table("document_chunk")
    # Leave the extension installed — other objects may rely on it; dropping is intentionally
    # NOT done here to avoid breaking a partially-downgraded DB.
