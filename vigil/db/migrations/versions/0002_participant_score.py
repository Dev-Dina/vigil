"""participant_score table with sponsor RLS (Phase 4 scoring writeback)

No platform bypass on this policy: ML admin + auditor carry scope:[] which resolves
app.current_sponsor to '' -> NULLIF -> NULL -> matches no rows. The API layer
additionally rejects with 403. Both layers are independently asserted in the leakage test.

Revision ID: 0002_participant_score
Revises: 0001_initial_rls
Create Date: 2026-06-09
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "0002_participant_score"
down_revision = "0001_initial_rls"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "participant_score",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column(
            "participant_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("participant.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "sponsor_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("sponsor.id", ondelete="RESTRICT"),
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
        sa.Column("risk_score", sa.Float, nullable=False),
        sa.Column("risk_band", sa.Text, nullable=False),
        sa.Column(
            "top_factors",
            pg.ARRAY(sa.Text),
            nullable=False,
            server_default="{}",
        ),
        sa.Column(
            "reasons",
            pg.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column("model_version", sa.Text, nullable=False),
        sa.Column("model_card_ref", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "synthetic",
            sa.Boolean,
            nullable=False,
            server_default=sa.true(),
        ),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("risk_score BETWEEN 0 AND 1", name="ck_ps_risk_score"),
        sa.CheckConstraint(
            "risk_band IN ('high','medium','low')", name="ck_ps_risk_band"
        ),
        sa.UniqueConstraint(
            "participant_id", "model_version", name="uq_participant_score"
        ),
    )
    op.create_index(
        "ix_ps_sponsor_trial", "participant_score", ["sponsor_id", "trial_id"]
    )
    op.create_index(
        "ix_ps_sponsor_band", "participant_score", ["sponsor_id", "risk_band"]
    )

    # RLS: no platform bypass — app.current_sponsor='' -> NULLIF -> NULL -> no rows.
    sponsor_guc = "NULLIF(current_setting('app.current_sponsor', true), '')"
    op.execute("ALTER TABLE participant_score ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE participant_score FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY participant_score_sponsor_isolation ON participant_score
        USING (sponsor_id = {sponsor_guc}::uuid)
        WITH CHECK (sponsor_id = {sponsor_guc}::uuid)
        """
    )


def downgrade() -> None:
    op.drop_index("ix_ps_sponsor_band", table_name="participant_score")
    op.drop_index("ix_ps_sponsor_trial", table_name="participant_score")
    op.drop_table("participant_score")
