"""engagement table + participant clinical covariates (B2a-1, specs/scoring.md § Engagement)

Adds:
  1. Columns to ``participant``: three imputed-capable covariates (age_years, hba1c_pct, bmi)
     each with a companion ``*_baseline_imputed`` boolean, plus ``sex``. All nullable so
     existing seed rows are not invalidated (null = covariate not yet recorded). When a
     covariate IS present the companion flag must also be present — enforced at application
     layer by the single-feature-path contract, not a DB CHECK (the DB constraint would be a
     partial NOT NULL which SQLite/Postgres compat makes awkward; the invariant is owned by
     the application + test suite per scoring.md § Inv 10). ``planned_duration_days`` is
     trial-level (read from trial record at feature-assembly time); it does NOT go on
     participant and carries no flag (spec correction B2a-spec-2 decision c).

  2. ``engagement`` table: per-participant visit trajectories backing the LSTM sequence model.
     Tenant-scoped (sponsor_id NOT NULL, FK→sponsor). FORCE RLS, no platform bypass, fail-
     closed. NOT in the RLS-exempt list (domain.md: a table is RLS-exempt only if it holds no
     per-sponsor participant data; engagement holds exactly that).

RLS EXEMPTION NON-JUSTIFICATION for engagement:
    engagement holds per-sponsor per-participant visit trajectory data.
    It is explicitly NOT RLS-exempt. The same sponsor-isolation policy as
    participant_score is applied: FORCE RLS, sponsor_id = GUC, fail-closed.
    Reviewed against specs/scoring.md § Engagement (visit trajectory) input § Tenancy.

Revision ID: 0004_engagement_participant_covariates
Revises: 0003_routing_state
Create Date: 2026-06-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0004_engagement"
down_revision = "0003_routing_state"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # ------------------------------------------------------------------
    # 1. participant clinical covariates
    # ------------------------------------------------------------------
    # All nullable: existing seed rows have no recorded values.
    # Imputed-capable columns always travel with their *_baseline_imputed companion.
    # sex is always-real categorical; no companion flag (spec § Static covariates home).
    for col, typ in [
        ("age_years", sa.Float),
        ("hba1c_pct", sa.Float),
        ("bmi", sa.Float),
    ]:
        op.add_column("participant", sa.Column(col, typ, nullable=True))
        op.add_column(
            "participant",
            sa.Column(f"{col}_baseline_imputed", sa.Boolean, nullable=True),
        )
    op.add_column("participant", sa.Column("sex", sa.String(16), nullable=True))

    # ------------------------------------------------------------------
    # 2. engagement table
    # ------------------------------------------------------------------
    op.create_table(
        "engagement",
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
        sa.Column("visit_index", sa.Integer, nullable=False),
        sa.Column(
            "visit_timestamp",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.Column("attended", sa.Boolean, nullable=False),
        sa.Column("missed", sa.Boolean, nullable=False),
        sa.Column("cumulative_missed", sa.Integer, nullable=False),
        sa.Column("consecutive_missed", sa.Integer, nullable=False),
        sa.Column(
            "synthetic",
            sa.Boolean,
            nullable=False,
            server_default=sa.false(),
        ),
        sa.CheckConstraint("visit_index >= 0", name="ck_eng_visit_index_nonneg"),
        sa.CheckConstraint(
            "cumulative_missed >= 0", name="ck_eng_cumulative_missed_nonneg"
        ),
        sa.CheckConstraint(
            "consecutive_missed >= 0", name="ck_eng_consecutive_missed_nonneg"
        ),
        sa.UniqueConstraint(
            "participant_id", "visit_index", name="uq_engagement_participant_visit"
        ),
    )
    # Hot-path index: all engagement rows for a trial scoped to a sponsor.
    op.create_index("ix_eng_sponsor_trial", "engagement", ["sponsor_id", "trial_id"])

    # RLS: mirror participant_score exactly — no platform bypass.
    # app.current_sponsor='' -> NULLIF -> NULL -> no rows (fail-closed).
    # NOT RLS-exempt: engagement holds per-sponsor participant trajectory data.
    sponsor_guc = "NULLIF(current_setting('app.current_sponsor', true), '')"
    op.execute("ALTER TABLE engagement ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE engagement FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY engagement_sponsor_isolation ON engagement
        USING (sponsor_id = {sponsor_guc}::uuid)
        WITH CHECK (sponsor_id = {sponsor_guc}::uuid)
        """
    )


def downgrade() -> None:
    op.drop_index("ix_eng_sponsor_trial", table_name="engagement")
    op.drop_table("engagement")
    for col in (
        "sex",
        "bmi_baseline_imputed",
        "bmi",
        "hba1c_pct_baseline_imputed",
        "hba1c_pct",
        "age_years_baseline_imputed",
        "age_years",
    ):
        op.drop_column("participant", col)
