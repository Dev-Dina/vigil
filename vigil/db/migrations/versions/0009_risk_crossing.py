"""risk_crossing table with sponsor RLS (Phase 9, Gate 9.2 — serious-risk crossing detection)

Tenant-scoped, same RLS guarantee as participant_score (no platform bypass: scope:[] resolves
app.current_sponsor to '' -> NULLIF -> NULL -> matches no rows). Records a non-high -> high
champion-point transition. Idempotent anchor: UNIQUE(crossing_score_id) — one crossing per the
champion score row that crossed.

Revision ID: 0009_risk_crossing
Revises: 0008_document_chunk
Create Date: 2026-06-17
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "0009_risk_crossing"
down_revision = "0008_document_chunk"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "risk_crossing",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "sponsor_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("sponsor.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "participant_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("participant.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "trial_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("trial.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "site_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("site.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "crossing_score_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("participant_score.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("model_version", sa.Text, nullable=False),
        sa.Column("risk_score", sa.Float, nullable=False),
        sa.Column("risk_band", sa.Text, nullable=False),
        sa.Column("prior_band", sa.Text, nullable=False),
        sa.Column("synthetic", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column(
            "notified", sa.Boolean, nullable=False, server_default=sa.false()
        ),
        sa.Column(
            "detected_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("risk_score BETWEEN 0 AND 1", name="ck_rc_risk_score"),
        sa.CheckConstraint("risk_band = 'high'", name="ck_rc_risk_band_high"),
        sa.CheckConstraint(
            "prior_band IN ('low','medium','none')", name="ck_rc_prior_band"
        ),
        # Idempotent anchor: one crossing per champion score row that crossed.
        sa.UniqueConstraint("crossing_score_id", name="uq_risk_crossing_score"),
    )
    op.create_index(
        "ix_risk_crossing_sponsor_site", "risk_crossing", ["sponsor_id", "site_id"]
    )
    op.create_index("ix_risk_crossing_notified", "risk_crossing", ["notified"])

    # RLS: no platform bypass — app.current_sponsor='' -> NULLIF -> NULL -> no rows.
    sponsor_guc = "NULLIF(current_setting('app.current_sponsor', true), '')"
    op.execute("ALTER TABLE risk_crossing ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE risk_crossing FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY risk_crossing_sponsor_isolation ON risk_crossing
        USING (sponsor_id = {sponsor_guc}::uuid)
        WITH CHECK (sponsor_id = {sponsor_guc}::uuid)
        """
    )


def downgrade() -> None:
    op.drop_index("ix_risk_crossing_notified", table_name="risk_crossing")
    op.drop_index("ix_risk_crossing_sponsor_site", table_name="risk_crossing")
    op.drop_table("risk_crossing")
