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

from vigil.db.models import AuditLog, ParticipantScore, RiskCrossing


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


def record_crossing(
    session: Session,
    *,
    score_row: ParticipantScore,
    prior_band: str | None,
) -> RiskCrossing | None:
    """Record a serious-risk crossing for ``score_row`` (non-high → high) — IDEMPOTENTLY.

    Anchored on ``crossing_score_id`` (UNIQUE): the champion score row that crossed is recorded
    at most once, so a double-detection of the SAME row never duplicates. The TRANSITION semantic
    (caller only calls this when the prior champion band was non-high and the new band is high)
    plus this anchor give the dedupe contract: a re-score while still high, and a retried job
    whose prior band is now high, both produce NO crossing. Carries the participant's full
    (sponsor, trial, site) scope for later scope-bound recipient resolution (Gate 9.5). Runs under
    the caller's RLS-scoped (sponsor-bound) session — the row inserts under that sponsor.
    """
    existing = session.execute(
        select(RiskCrossing).where(RiskCrossing.crossing_score_id == score_row.id)
    ).scalar_one_or_none()
    if existing is not None:
        return existing
    row = RiskCrossing(
        sponsor_id=score_row.sponsor_id,
        participant_id=score_row.participant_id,
        trial_id=score_row.trial_id,
        site_id=score_row.site_id,
        crossing_score_id=score_row.id,
        model_version=score_row.model_version,
        risk_score=score_row.risk_score,
        risk_band=score_row.risk_band,
        prior_band=prior_band if prior_band in ("low", "medium") else "none",
        synthetic=score_row.synthetic,
    )
    session.add(row)
    session.flush()
    return row


def list_crossings_for_participant(
    session: Session, participant_id: uuid.UUID
) -> list[RiskCrossing]:
    """All serious-risk crossings for a participant (newest first) under RLS scope."""
    return list(
        session.execute(
            select(RiskCrossing)
            .where(RiskCrossing.participant_id == participant_id)
            .order_by(RiskCrossing.detected_at.desc())
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
