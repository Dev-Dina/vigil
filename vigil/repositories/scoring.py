"""Scoring repository — the only DB access path for participant_score.

All writes/reads run under RLS via the scope-bound session passed in by the caller.
The session's app.current_sponsor GUC determines which rows are visible; this module
never adds a WHERE sponsor_id clause to compensate (RLS is the hard guarantee).
"""

from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.orm import Session

from vigil.db.models import AuditLog, ParticipantScore


def upsert_score(
    session: Session,
    *,
    participant_id: uuid.UUID,
    sponsor_id: uuid.UUID,
    trial_id: uuid.UUID,
    site_id: uuid.UUID,
    risk_score: float,
    risk_band: str,
    top_factors: list[str],
    reasons: list[Any],
    model_version: str,
    model_card_ref: str,
    synthetic: bool,
) -> ParticipantScore:
    """INSERT ... ON CONFLICT (participant_id, model_version) DO UPDATE SET ...

    Idempotent by the unique key; safe to retry.
    """
    stmt = (
        insert(ParticipantScore)
        .values(
            participant_id=participant_id,
            sponsor_id=sponsor_id,
            trial_id=trial_id,
            site_id=site_id,
            risk_score=risk_score,
            risk_band=risk_band,
            top_factors=top_factors,
            reasons=reasons,
            model_version=model_version,
            model_card_ref=model_card_ref,
            synthetic=synthetic,
        )
        .on_conflict_do_update(
            constraint="uq_participant_score",
            set_={
                "sponsor_id": sponsor_id,
                "trial_id": trial_id,
                "site_id": site_id,
                "risk_score": risk_score,
                "risk_band": risk_band,
                "top_factors": top_factors,
                "reasons": reasons,
                "model_card_ref": model_card_ref,
                "synthetic": synthetic,
            },
        )
        .returning(ParticipantScore)
    )
    row = session.execute(stmt).scalar_one()
    session.flush()
    return row


def get_score(
    session: Session, participant_id: uuid.UUID, model_version: str
) -> ParticipantScore | None:
    return session.execute(
        select(ParticipantScore).where(
            ParticipantScore.participant_id == participant_id,
            ParticipantScore.model_version == model_version,
        )
    ).scalar_one_or_none()


def list_scores_for_participant(
    session: Session, participant_id: uuid.UUID
) -> list[ParticipantScore]:
    return list(
        session.execute(
            select(ParticipantScore).where(
                ParticipantScore.participant_id == participant_id
            )
        ).scalars()
    )


def write_score_audit(
    session: Session,
    *,
    sponsor_id: uuid.UUID,
    participant_id: uuid.UUID,
    model_version: str,
    synthetic: bool,
    n_rows: int = 1,
) -> None:
    """Append an AuditLog row for a score writeback.

    No PII in detail. No risk values in detail. actor_user_id=None (job, not human).
    """
    row = AuditLog(
        actor_user_id=None,
        action="score_writeback",
        target_type="participant_score",
        target_id=str(participant_id),
        sponsor_id=sponsor_id,
        detail={
            "model_version": model_version,
            "synthetic": synthetic,
            "n_rows": n_rows,
        },
    )
    session.add(row)
    session.flush()
