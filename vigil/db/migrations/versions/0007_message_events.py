"""message_events table — Phase-5 turn audit record (specs/observability.md).

One row per chatbot/assistant turn on BOTH surfaces, written AFTER redaction (raw text is
never stored — there is no raw column). Carries the redacted user/assistant text, the
guardrail decision, retrieved-chunk citation refs, model/latency/cost, and status.

RLS — CROSS-TENANT BY ROLE (domain.md "Cross-tenant by design": user/assignment_grant/
audit_log/message_events). This is NOT the default sponsor-only predicate: a CRO spans
sponsors and auditor/admin read across, so the policy mirrors ``audit_scope`` exactly —
``USING (is_platform OR sponsor_id = current_sponsor)``. Consequences:
  - a sponsor session sees only its own rows (sponsor_id = GUC),
  - platform/auditor (is_platform=on) see all rows,
  - null-sponsor (Guide/platform) rows are platform-visible ONLY — invisible to any sponsor
    session, because ``NULL = GUC::uuid`` is never true.
FORCE RLS so the policy binds even for the table owner. message_events is bespoke (like
audit_log) — deliberately NOT added to TENANT_TABLES.

Applies cleanly on a fresh DB and on the existing 0001–0006 chain.

Revision ID: 0007_message_events
Revises: 0006_participant_score_history
Create Date: 2026-06-15
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql as pg

revision = "0007_message_events"
down_revision = "0006_participant_score_history"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "message_events",
        sa.Column(
            "id",
            pg.UUID(as_uuid=True),
            primary_key=True,
            server_default=sa.text("gen_random_uuid()"),
        ),
        sa.Column("conversation_id", pg.UUID(as_uuid=True), nullable=False),
        sa.Column("request_id", sa.String(64), nullable=False),
        # Nullable: Guide/platform turns are null-sponsor (cross-tenant-by-role, like audit_log).
        sa.Column(
            "sponsor_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("sponsor.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("role_or_guest_scope", sa.String(64), nullable=False),
        sa.Column("surface", sa.String(16), nullable=False),
        sa.Column("route_or_agent", sa.String(128), nullable=False, server_default=""),
        sa.Column("guardrail_decision", sa.String(16), nullable=False),
        sa.Column(
            "retrieved_chunks",
            pg.JSONB,
            nullable=False,
            server_default=sa.text("'[]'::jsonb"),
        ),
        sa.Column(
            "llm_provider_model", sa.String(128), nullable=False, server_default=""
        ),
        sa.Column(
            "latency_ms", sa.Integer, nullable=False, server_default=sa.text("0")
        ),
        sa.Column(
            "token_cost_estimate",
            sa.Numeric(12, 6),
            nullable=False,
            server_default=sa.text("0"),
        ),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("redacted_user_msg", sa.Text, nullable=False, server_default=""),
        sa.Column("redacted_assistant_msg", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "ts",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
    )
    op.create_index(
        "ix_msg_conversation_ts", "message_events", ["conversation_id", "ts"]
    )
    op.create_index("ix_msg_sponsor_ts", "message_events", ["sponsor_id", "ts"])

    # RLS: cross-tenant-by-role, mirror audit_scope EXACTLY (NOT the default sponsor predicate).
    # NULLIF('' -> NULL) so an unset GUC matches nothing (fail-closed) without a uuid cast error;
    # the is_platform OR-branch lets platform/auditor read across (incl. null-sponsor Guide rows).
    sponsor_guc = "NULLIF(current_setting('app.current_sponsor', true), '')"
    is_platform = "current_setting('app.is_platform', true) = 'on'"
    op.execute("ALTER TABLE message_events ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE message_events FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY message_events_scope ON message_events
        USING ({is_platform} OR sponsor_id = {sponsor_guc}::uuid)
        WITH CHECK ({is_platform} OR sponsor_id = {sponsor_guc}::uuid)
        """
    )


def downgrade() -> None:
    op.execute("DROP POLICY IF EXISTS message_events_scope ON message_events")
    op.drop_index("ix_msg_sponsor_ts", table_name="message_events")
    op.drop_index("ix_msg_conversation_ts", table_name="message_events")
    op.drop_table("message_events")
