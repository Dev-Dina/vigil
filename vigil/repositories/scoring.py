"""Scoring repository — the only DB access path for participant_score.

All writes/reads run under RLS via the scope-bound session passed in by the caller.
The session's app.current_sponsor GUC determines which rows are visible; this module
never adds a WHERE sponsor_id clause to compensate (RLS is the hard guarantee).
"""

from __future__ import annotations

import uuid
from collections.abc import Collection
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from vigil.db.models import AuditLog, ParticipantScore


def append_score(
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
    """APPEND a new timestamped participant_score row (history; H1).

    Plain INSERT — does NOT overwrite a prior row. Each scoring run adds a new point
    (new ``computed_at``); prior rows for the same (participant, model_version) remain as
    history. Re-running appends rather than mutating in place (idempotency change per
    specs/scoring.md § Writeback). Runs under the caller's RLS-scoped session.
    """
    row = ParticipantScore(
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
    session.add(row)
    session.flush()
    return row


def get_score(
    session: Session, participant_id: uuid.UUID, model_version: str
) -> ParticipantScore | None:
    """Newest participant_score row for a (participant, model_version) — history-safe.

    With appended history multiple rows can share a (participant_id, model_version); this
    returns the latest by ``computed_at`` rather than raising on multiple matches.
    """
    return session.execute(
        select(ParticipantScore)
        .where(
            ParticipantScore.participant_id == participant_id,
            ParticipantScore.model_version == model_version,
        )
        .order_by(ParticipantScore.computed_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def get_surfaceable_score(
    session: Session,
    participant_id: uuid.UUID,
    *,
    champion_versions: Collection[str],
) -> ParticipantScore | None:
    """Champion-only read for clinical surfaces (``GET /participants/{id}/risk``).

    Filters ``model_version`` to the champion allowlist BY CONSTRUCTION — a
    challenger/shadow row can never be returned because its version is not in
    ``champion_versions``. An empty allowlist returns ``None`` (fail closed): with no
    known champion, nothing is surfaceable rather than defaulting to an arbitrary row.
    Runs under the caller's RLS-scoped session, so tenant isolation still applies.
    """
    if not champion_versions:
        return None
    return session.execute(
        select(ParticipantScore)
        .where(
            ParticipantScore.participant_id == participant_id,
            ParticipantScore.model_version.in_(list(champion_versions)),
        )
        .order_by(ParticipantScore.computed_at.desc())
        .limit(1)
    ).scalar_one_or_none()


def champion_scores_by_participant(
    session: Session,
    participant_ids: Collection[uuid.UUID],
    *,
    champion_versions: Collection[str],
) -> dict[uuid.UUID, ParticipantScore]:
    """Latest champion ``participant_score`` row per participant — the BATCH champion-only
    read for the cohort surface (same allowlist guard as ``get_surfaceable_score``).

    Filters ``model_version`` to the champion allowlist BY CONSTRUCTION, so shadow/challenger
    rows can never be surfaced. Empty allowlist or no champion row → participant simply absent
    from the returned map (caller treats it as "no champion score yet", fail-closed). Runs
    under the caller's RLS-scoped session, so tenant isolation still applies.
    """
    if not champion_versions or not participant_ids:
        return {}
    rows = session.execute(
        select(ParticipantScore)
        .where(
            ParticipantScore.participant_id.in_(list(participant_ids)),
            ParticipantScore.model_version.in_(list(champion_versions)),
        )
        .order_by(ParticipantScore.computed_at.desc())
    ).scalars()
    latest: dict[uuid.UUID, ParticipantScore] = {}
    for row in rows:  # rows are newest-first; keep the first seen per participant
        latest.setdefault(row.participant_id, row)
    return latest


def history_rows_for_participant(
    session: Session, participant_id: uuid.UUID
) -> list[ParticipantScore]:
    """ALL participant_score rows for a participant, oldest-first (every model_version).

    The champion-at-each-point filter (semantic (b)) is applied by the caller against the
    champion timeline (``routing.champion_version_intervals``); this returns the raw rows
    under the caller's RLS-scoped session (tenant isolation still applies). Shadow/challenger
    rows are returned here and EXCLUDED by the caller's timeline filter — they are never a
    champion-of-record at their own timestamp.
    """
    return list(
        session.execute(
            select(ParticipantScore)
            .where(ParticipantScore.participant_id == participant_id)
            .order_by(ParticipantScore.computed_at.asc())
        ).scalars()
    )


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
