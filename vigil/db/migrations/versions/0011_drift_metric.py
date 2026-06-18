"""drift_metric table — computed PSI/KS drift points (Gate M1)

Platform-scoped, RLS-EXEMPT (same tier as routing_state): it stores ONLY aggregate model
statistics (scalar PSI/KS values over the model's pooled output distribution) — no sponsor_id, no
participant, nothing tenant-identifying — so no RLS predicate fits and none is needed. The read
surface (GET /monitoring/drift) is platform/auditor-gated at the app layer. Migration runs as the
owner. NOT in TENANT_TABLES (no sponsor RLS).

Revision ID: 0011_drift_metric
Revises: 0010_user_notification_email
Create Date: 2026-06-19
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "0011_drift_metric"
down_revision = "0010_user_notification_email"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "drift_metric",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("regime", sa.String(128), nullable=False),
        sa.Column("model_version", sa.String(128), nullable=False),
        sa.Column("distribution", sa.String(128), nullable=False),
        sa.Column("metric", sa.String(16), nullable=False),
        sa.Column("value", sa.Float, nullable=False),
        sa.Column("threshold", sa.Float, nullable=False),
        sa.Column("breached", sa.Boolean, nullable=False),
        sa.Column("reference_n", sa.Integer, nullable=False),
        sa.Column("current_n", sa.Integer, nullable=False),
        sa.Column("synthetic", sa.Boolean, nullable=False, server_default=sa.true()),
        sa.Column(
            "constructed_demo", sa.Boolean, nullable=False, server_default=sa.false()
        ),
        sa.Column("note", sa.String(512), nullable=False, server_default=""),
        sa.Column(
            "computed_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint("metric IN ('psi','ks')", name="ck_drift_metric_kind"),
    )
    op.create_index("ix_drift_metric_computed_at", "drift_metric", ["computed_at"])
    op.create_index(
        "ix_drift_metric_regime_version", "drift_metric", ["regime", "model_version"]
    )
    # NO row-level security: platform-tier table, no tenant key (mirrors routing_state).


def downgrade() -> None:
    op.drop_index("ix_drift_metric_regime_version", table_name="drift_metric")
    op.drop_index("ix_drift_metric_computed_at", table_name="drift_metric")
    op.drop_table("drift_metric")
