"""routing_state table — platform-scoped model routing registry (specs/routing.md)

RLS EXEMPTION JUSTIFICATION (domain.md § Tenancy rules: every RLS exemption must be
justified in the migration that creates the table):
    routing_state holds no per-sponsor operational or participant data. It is a global
    infrastructure table: routing decisions (regime → champion model version) apply
    across all tenants simultaneously. It belongs to the "model registry / routing
    tables" tier listed as RLS-exempt in domain.md § Tenancy rules. No sponsor_id
    column exists, so there is no predicate to attach a standard sponsor policy to.
    Sponsor-scoped sessions are blocked at the application layer: only platform-scoped
    endpoints query this table (specs/routing.md § Tenancy). Reviewed against
    specs/routing.md § Decisions (fixed) and § Regime routing.

Revision ID: 0003_routing_state
Revises: 0002_participant_score
Create Date: 2026-06-10
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0003_routing_state"
down_revision = "0002_participant_score"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "routing_state",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("regime", sa.Text, nullable=False),
        sa.Column("role", sa.Text, nullable=False),
        sa.Column("model_version", sa.Text, nullable=False),
        sa.Column("model_card_ref", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "health",
            sa.Text,
            nullable=False,
            server_default="healthy",
        ),
        sa.Column(
            "promoted_at",
            sa.DateTime(timezone=True),
            nullable=False,
            server_default=sa.text("now()"),
        ),
        sa.Column(
            "promoted_by",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.CheckConstraint(
            "role IN ('champion', 'challenger', 'shadow')",
            name="ck_rs_role",
        ),
        sa.CheckConstraint(
            "health IN ('healthy', 'degraded', 'fallback')",
            name="ck_rs_health",
        ),
        sa.UniqueConstraint("regime", "role", name="uq_routing_state_regime_role"),
    )
    # No RLS attached: routing_state is platform-scoped / RLS-exempt.
    # See module docstring for the justification required by domain.md.


def downgrade() -> None:
    op.drop_table("routing_state")
