"""participant_score becomes appended history (H1, specs/scoring.md § Writeback).

Replaces the upsert-overwrite key with append-history identity:
  - DROP UNIQUE(participant_id, model_version) (constraint ``uq_participant_score``) — it
    forced one row per participant per version, overwritten each run.
  - ADD composite index ``ix_ps_participant_version_time`` on
    (participant_id, model_version, computed_at) — serves the newest-champion-row read and
    the risk-history range query. Identity is now the surrogate ``id`` PK; rows APPEND.

RLS is UNCHANGED (participant_score keeps its FORCE, fail-closed sponsor policy from 0002).
Applies cleanly on a fresh DB and on the existing 0001–0005 chain.

Revision ID: 0006_participant_score_history
Revises: 0005_participant_trial_context
Create Date: 2026-06-11
"""

from __future__ import annotations

from alembic import op

revision = "0006_participant_score_history"
down_revision = "0005_participant_trial_context"
branch_labels = None
depends_on = None


def upgrade() -> None:
    # Drop the upsert-forcing unique constraint; rows now append as history.
    op.drop_constraint("uq_participant_score", "participant_score", type_="unique")
    # Composite index for newest-row (ORDER BY computed_at DESC) + history range scans.
    op.create_index(
        "ix_ps_participant_version_time",
        "participant_score",
        ["participant_id", "model_version", "computed_at"],
    )


def downgrade() -> None:
    op.drop_index("ix_ps_participant_version_time", table_name="participant_score")
    # Restoring the unique constraint requires no duplicate (participant_id, model_version)
    # rows to exist — i.e. history collapsed to one row per version first.
    op.create_unique_constraint(
        "uq_participant_score",
        "participant_score",
        ["participant_id", "model_version"],
    )
