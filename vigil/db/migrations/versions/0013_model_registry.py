"""model_registry table — the model registry / register-validated-weights catalog (Gate M3)

Platform-scoped, RLS-EXEMPT (same tier as routing_state / drift_metric): it carries ONLY model-
governance metadata — a registered version's identifier, artifact reference, the OFFLINE validation
metrics SUPPLIED with it (recorded, never computed in-app), and its provenance label — NO tenant
data, no sponsor_id, so no RLS predicate fits and none is needed. The write surface (register /
promote) is platform_admin-only at the app layer; the read surface (GET /monitoring/registry) is
platform/auditor-only. NOT in TENANT_TABLES.

Revision ID: 0013_model_registry
Revises: 0012_drift_metric_notified
Create Date: 2026-06-19
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "0013_model_registry"
down_revision = "0012_drift_metric_notified"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "model_registry",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("regime", sa.String(128), nullable=False),
        sa.Column("model_version", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="challenger"),
        sa.Column("artifact_ref", sa.String(512), nullable=False),
        sa.Column("model_card_ref", sa.String(512), nullable=False),
        sa.Column(
            "metrics",
            pg.JSONB,
            nullable=False,
            server_default=sa.text("'{}'::jsonb"),
        ),
        sa.Column("provenance", sa.String(128), nullable=False),
        sa.Column("notes", sa.String(512), nullable=False, server_default=""),
        sa.Column(
            "registered_by",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "registered_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.CheckConstraint(
            "status IN ('challenger','shadow','champion','retired')",
            name="ck_model_registry_status",
        ),
        sa.UniqueConstraint(
            "regime", "model_version", name="uq_model_registry_version"
        ),
    )
    op.create_index(
        "ix_model_registry_regime_status", "model_registry", ["regime", "status"]
    )
    # NO row-level security: platform-tier table, no tenant key (mirrors routing_state).


def downgrade() -> None:
    op.drop_index("ix_model_registry_regime_status", table_name="model_registry")
    op.drop_table("model_registry")
