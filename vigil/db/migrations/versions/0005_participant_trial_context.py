"""Trial context columns + participant arm_type (B2b, specs/scoring.md § Static covariates).

Adds:
  1. Columns to ``trial``: ``n_sites`` (Integer, nullable), ``planned_duration_days``
     (Integer, nullable), ``phase`` (String(64), nullable).
     ``planned_duration_days`` is trial-level per spec correction B2a-spec-2 decision c;
     it is NOT on participant and carries no baseline_imputed flag.

  2. Column to ``participant``: ``arm_type`` (String(64), nullable).
     Per-arm context derived from the seed bridge parquet; nullable so existing rows
     are not invalidated. No companion flag (not an imputed-capable covariate).

These four columns complete the eight static features the LSTM StaticEncoder requires:
  STATIC_NUMERIC: age_years, hba1c_pct, bmi, n_sites, planned_duration_days
  STATIC_CATEGORICAL: sex, phase, arm_type

Revision ID: 0005_participant_trial_context
Revises: 0004_engagement
Create Date: 2026-06-11
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op

revision = "0005_participant_trial_context"
down_revision = "0004_engagement"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # 1. Trial context features (trial-level; NOT on participant)
    for col, typ in [
        ("n_sites", sa.Integer),
        ("planned_duration_days", sa.Integer),
    ]:
        op.add_column("trial", sa.Column(col, typ, nullable=True))
    op.add_column("trial", sa.Column("phase", sa.String(64), nullable=True))

    # 2. Participant arm_type (per-arm; no companion flag)
    op.add_column("participant", sa.Column("arm_type", sa.String(64), nullable=True))


def downgrade() -> None:
    op.drop_column("participant", "arm_type")
    op.drop_column("trial", "phase")
    op.drop_column("trial", "planned_duration_days")
    op.drop_column("trial", "n_sites")
