"""Participants router (api.md /participants).

Scope-guarded: platform roles (PLATFORM_ADMIN, AUDITOR) receive 403 on all
participant-level routes — scope:[] carries no participant access
(domain.md, specs/dashboard.md § Role-scoped rendering).

Repository calls are stubs; real DB wiring is Phase 5.
"""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from vigil.api.deps import ScopeDep
from vigil.core.schemas import Page
from vigil.core.scope import Scope
from vigil.domain import IDENTITY_ROLES, PLATFORM_ROLES, InterventionKind

router = APIRouter(prefix="/participants", tags=["participants"])


# ---------------------------------------------------------------------------
# Response schemas (api.md)
# ---------------------------------------------------------------------------


class ParticipantIdentity(BaseModel):
    """Populated only for site roles (PI / coordinator); null otherwise (api.md)."""

    coded_ref: str | None = None


class ParticipantDetail(BaseModel):
    participant_id: str
    trial_id: str
    site_id: str
    status: str
    risk_score: float = Field(ge=0, le=1)
    enrolled_at: datetime
    synthetic: bool  # from participant_score.synthetic; always surfaced (dashboard.md)
    identity: ParticipantIdentity | None = None


class FactorContribution(BaseModel):
    feature: str
    contribution: float  # signed; sign encodes direction


class RiskExplanation(BaseModel):
    participant_id: str
    risk_score: float
    horizon_days: int = 28
    factors: list[FactorContribution]
    model_version: str


class InterventionIn(BaseModel):
    kind: InterventionKind
    note: str = Field(max_length=2000)


class InterventionOut(BaseModel):
    id: str
    participant_id: str
    kind: str
    note: str
    actor_user_id: str
    created_at: datetime


# ---------------------------------------------------------------------------
# Scope guard helpers
# ---------------------------------------------------------------------------


def _deny_platform(scope: Scope) -> None:
    """Platform roles must never reach participant data (domain.md)."""
    if scope.role in PLATFORM_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="platform roles may not access participant data",
        )


def _deny_auditor_write(scope: Scope) -> None:
    """Auditors are read-only — no write actions (dashboard.md)."""
    from vigil.domain import Role

    if scope.role is Role.AUDITOR:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="auditor may not log interventions",
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.get("/{participant_id}", response_model=ParticipantDetail)
async def get_participant(
    participant_id: str,
    scope: Scope = ScopeDep,
) -> ParticipantDetail:
    """GET /participants/{participant_id} — 403 for platform roles."""
    _deny_platform(scope)
    # TODO(phase5): wire real DB query via participant repository (scoped session).
    # Populate identity only for site roles.
    identity: ParticipantIdentity | None = None
    if scope.role in IDENTITY_ROLES:
        identity = ParticipantIdentity(coded_ref=participant_id)
    return ParticipantDetail(
        participant_id=participant_id,
        trial_id="trl_stub",
        site_id="sit_stub",
        status="active",
        risk_score=0.5,
        enrolled_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
        synthetic=True,  # TODO(phase5): read from participant_score.synthetic
        identity=identity,
    )


@router.get("/{participant_id}/risk", response_model=RiskExplanation)
async def get_risk(
    participant_id: str,
    scope: Scope = ScopeDep,
) -> RiskExplanation:
    """GET /participants/{participant_id}/risk — champion-only; 403 for platform roles.

    Champion-only surfacing (specs/routing.md § Champion/challenger/shadow, layer 2):
    the score is read through the champion allowlist filter, so a shadow/challenger row
    can never be surfaced. Fails closed with 404 when no champion score exists — never a
    non-champion fallback.
    """
    from vigil.services import risk_service

    _deny_platform(scope)
    view = risk_service.get_participant_risk(scope, participant_id)
    if view is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="no champion risk score for participant",
        )
    return RiskExplanation(
        participant_id=view.participant_id,
        risk_score=view.risk_score,
        horizon_days=28,
        factors=[
            FactorContribution(feature=f.feature, contribution=f.contribution)
            for f in view.factors
        ],
        model_version=view.model_version,
    )


@router.post(
    "/{participant_id}/interventions",
    status_code=201,
    response_model=InterventionOut,
)
async def log_intervention(
    participant_id: str,
    body: InterventionIn,
    scope: Scope = ScopeDep,
) -> InterventionOut:
    """POST interventions — 403 for platform roles and auditor (write)."""
    _deny_platform(scope)
    _deny_auditor_write(scope)
    # TODO(phase5): wire to intervention repository; write audit log row.
    return InterventionOut(
        id="iv_stub",
        participant_id=participant_id,
        kind=body.kind,
        note=body.note,
        actor_user_id=scope.user_id,
        created_at=datetime.now(tz=timezone.utc),
    )


@router.get("/{participant_id}/interventions", response_model=Page)
async def list_interventions(
    participant_id: str,
    scope: Scope = ScopeDep,
) -> Page:
    """GET /participants/{participant_id}/interventions — 403 for platform roles."""
    _deny_platform(scope)
    # TODO(phase5): wire to intervention repository (scoped session).
    return Page(items=[], next_cursor=None, total=0)
