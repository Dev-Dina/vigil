"""Participant detail + interventions read/write path (api.md § participants).

Scope-bound: every DB access runs under the caller's RLS session, so an out-of-scope
participant is simply invisible (returns None → router 404). ``synthetic`` is sourced from the
CHAMPION participant_score row (B4 champion-only provenance); identity (coded_ref) is surfaced
ONLY for site roles (PI/coordinator), null otherwise (api.md, dashboard.md).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from vigil.core.scope import Scope
from vigil.db.models import AuditLog
from vigil.domain import IDENTITY_ROLES
from vigil.repositories import routing as routing_repo
from vigil.repositories import scoring as scoring_repo
from vigil.repositories import tenancy as tenancy_repo
from vigil.repositories.session import platform_session, scoped_session
from vigil.services import scope_filter


@dataclass(frozen=True, slots=True)
class ParticipantDetailView:
    participant_id: str
    trial_id: str
    site_id: str
    status: str
    risk_score: float
    enrolled_at: datetime
    synthetic: bool
    coded_ref: str | None  # populated ONLY for site roles; None otherwise


@dataclass(frozen=True, slots=True)
class InterventionView:
    id: str
    participant_id: str
    kind: str
    note: str
    actor_user_id: str
    created_at: datetime


def get_detail(scope: Scope, participant_id: str) -> ParticipantDetailView | None:
    """Real ParticipantDetail for an in-scope participant, or None (out-of-scope/not found)."""
    try:
        pid = uuid.UUID(participant_id)
    except (ValueError, AttributeError):
        return None

    with platform_session() as session:
        champion_versions = routing_repo.champion_model_versions(session)

    with scoped_session(scope) as session:
        p = tenancy_repo.get_participant(session, pid)
        if p is None:
            return None  # RLS-hidden or nonexistent → fail closed (404)
        # SEC-1: cross-site narrowing the sponsor-only RLS can't do (domain.md; matches 5.4 tools).
        if not scope_filter.participant_visible(
            scope, sponsor_id=p.sponsor_id, trial_id=p.trial_id, site_id=p.site_id
        ):
            return None  # out of the caller's trial/site scope → fail closed (404)
        champ = scoring_repo.get_surfaceable_score(
            session, pid, champion_versions=champion_versions
        )
    # synthetic from the champion score (B4); safe-synthetic default if unscored (never claims real).
    synthetic = champ.synthetic if champ is not None else True
    coded_ref = p.coded_ref if scope.role in IDENTITY_ROLES else None
    return ParticipantDetailView(
        participant_id=str(p.id),
        trial_id=str(p.trial_id),
        site_id=str(p.site_id),
        status=p.status,
        risk_score=p.risk_score,
        enrolled_at=p.enrolled_at,
        synthetic=synthetic,
        coded_ref=coded_ref,
    )


def log_intervention(
    scope: Scope, participant_id: str, *, kind: str, note: str
) -> InterventionView | None:
    """Append a real, audited intervention; the actor is the AUTHENTICATED user (server-side).

    Returns None if the participant is not visible to the caller (out-of-scope → 404). Writes
    an audit_log row alongside the intervention (nothing changes silently).
    """
    try:
        pid = uuid.UUID(participant_id)
    except (ValueError, AttributeError):
        return None
    actor = uuid.UUID(scope.user_id)
    requested_sponsor = next(iter(scope.sponsor_ids), None)

    with scoped_session(scope, sponsor_id=requested_sponsor) as session:
        p = tenancy_repo.get_participant(session, pid)
        if p is None:
            return None  # out-of-scope / not found → fail closed
        # SEC-1: cannot log an intervention on a participant outside the caller's trial/site scope.
        if not scope_filter.participant_visible(
            scope, sponsor_id=p.sponsor_id, trial_id=p.trial_id, site_id=p.site_id
        ):
            return None
        row = tenancy_repo.add_intervention(
            session,
            sponsor_id=p.sponsor_id,
            participant_id=pid,
            kind=kind,
            note=note,
            actor_user_id=actor,
        )
        session.add(
            AuditLog(
                actor_user_id=actor,
                action="intervention_logged",
                target_type="intervention",
                target_id=str(row.id),
                sponsor_id=p.sponsor_id,
                detail={"participant_id": str(pid), "kind": kind},
            )
        )
        session.flush()
        return InterventionView(
            id=str(row.id),
            participant_id=str(pid),
            kind=row.kind,
            note=row.note,
            actor_user_id=str(row.actor_user_id),
            created_at=row.created_at,
        )


def list_interventions(
    scope: Scope, participant_id: str
) -> list[InterventionView] | None:
    """Intervention history for an in-scope participant (newest-first), or None if not visible."""
    try:
        pid = uuid.UUID(participant_id)
    except (ValueError, AttributeError):
        return None
    with scoped_session(scope) as session:
        p = tenancy_repo.get_participant(session, pid)
        if p is None:
            return None
        # SEC-1: cross-site narrowing (domain.md; matches 5.4 tools) — out-of-site → fail closed.
        if not scope_filter.participant_visible(
            scope, sponsor_id=p.sponsor_id, trial_id=p.trial_id, site_id=p.site_id
        ):
            return None
        rows = tenancy_repo.list_interventions(session, pid)
        return [
            InterventionView(
                id=str(r.id),
                participant_id=str(r.participant_id),
                kind=r.kind,
                note=r.note,
                actor_user_id=str(r.actor_user_id),
                created_at=r.created_at,
            )
            for r in rows
        ]
