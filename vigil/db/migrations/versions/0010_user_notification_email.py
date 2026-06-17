"""user.notification_email — per-user serious-risk doorbell address (Phase 9, Gate 9.5)

A nullable column on the existing cross-tenant ``user`` table; the table's bespoke
``user_scope`` RLS policy (migration 0001) is UNCHANGED — this only adds an attribute. Defaults
UNSET for every user; set per user via PUT /me/notification-email. The personal demo address is
NEVER a column default and never a code constant (domain.md § Notification routing).

Revision ID: 0010_user_notification_email
Revises: 0009_risk_crossing
Create Date: 2026-06-18
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0010_user_notification_email"
down_revision = "0009_risk_crossing"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "user",
        sa.Column("notification_email", sa.String(length=320), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("user", "notification_email")
