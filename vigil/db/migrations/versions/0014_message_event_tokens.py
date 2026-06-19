"""message_events.prompt_tokens + completion_tokens — real per-turn token persistence (Gate COST-1)

The LLM clients already READ real token counts from live responses (Anthropic input/output_tokens;
OpenRouter prompt/completion_tokens) but they were discarded — only the derived dollar cost and
latency were stored. This adds the two integer columns so the REAL counts persist per turn and can
be aggregated at GET /monitoring/cost. Non-null, default 0 (honest-zero for no-generation paths and
the stub). message_events is cross-tenant-by-role (no default sponsor RLS); additive columns only.

Revision ID: 0014_message_event_tokens
Revises: 0013_model_registry
Create Date: 2026-06-19
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa

revision = "0014_message_event_tokens"
down_revision = "0013_model_registry"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "message_events",
        sa.Column("prompt_tokens", sa.Integer, nullable=False, server_default="0"),
    )
    op.add_column(
        "message_events",
        sa.Column("completion_tokens", sa.Integer, nullable=False, server_default="0"),
    )


def downgrade() -> None:
    op.drop_column("message_events", "completion_tokens")
    op.drop_column("message_events", "prompt_tokens")
