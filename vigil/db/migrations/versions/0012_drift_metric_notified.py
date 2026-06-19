"""drift_metric.notified — Gate M2 drift-breach ALERT send-once hook

Mirrors ``risk_crossing.notified`` (Gate 9.6): the ML-engineer drift-breach alert is sent EXACTLY
ONCE per breach event. The breach EVENT is the not-breached → breached EDGE (the producer only
enqueues an alert on a transition into breach, so re-runs while still breached do NOT re-alert);
``notified`` on the anchor point is the per-send dedupe so a RETRIED alert job never double-sends.
Platform-tier table (no RLS), so no policy change — just a non-null boolean defaulting false.

Revision ID: 0012_drift_metric_notified
Revises: 0011_drift_metric
Create Date: 2026-06-19
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0012_drift_metric_notified"
down_revision = "0011_drift_metric"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "drift_metric",
        sa.Column("notified", sa.Boolean, nullable=False, server_default=sa.false()),
    )
    op.create_index("ix_drift_metric_notified", "drift_metric", ["notified"])


def downgrade() -> None:
    op.drop_index("ix_drift_metric_notified", table_name="drift_metric")
    op.drop_column("drift_metric", "notified")
