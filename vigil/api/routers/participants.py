"""Participants router (api.md /participants).

Scope-guarded: platform roles (PLATFORM_ADMIN, AUDITOR) receive 403 on all
participant-level routes — scope:[] carries no participant access
(domain.md, specs/dashboard.md § Role-scoped rendering).

Wired to real scoped services (Wire-3): detail + /risk (champion-only) + /risk/history
(champion-at-each-point) + interventions (real, audited). Out-of-scope → 404 fail-closed.
"""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from vigil.api.deps import ScopeDep
from vigil.core.schemas import Page
from vigil.core.scope import Scope, ScopeError
from vigil.domain import PLATFORM_ROLES, SITE_ROLES, InterventionKind

router = APIRouter(prefix="/participants", tags=["participants"])


# ---------------------------------------------------------------------------
# Response schemas (api.md)
# ---------------------------------------------------------------------------


class ParticipantIdentity(BaseModel):
    """Populated only for site roles (PI / coordinator); null otherwise (api.md)."""

    coded_ref: str | None = None


class BaselineContext(BaseModel):
    """SYNTHETIC literature-prior baseline covariates — NOT real measurements. Returned ONLY for
    site roles (same gate as ``identity``/coded_ref); null otherwise. Each ``*_imputed`` flag marks a
    literature-prior imputation; the read surface MUST render an imputed value WITH a synthetic/
    imputed label (never as a bare clinical value). These covariates also feed the scoring model's
    static context (they move the score), but only the visit-trajectory features receive attribution.
    """

    age_years: float | None = None
    age_imputed: bool = False
    hba1c_pct: float | None = None
    hba1c_imputed: bool = False
    bmi: float | None = None
    bmi_imputed: bool = False
    sex: str | None = None
    arm_type: str | None = None


class ParticipantDetail(BaseModel):
    participant_id: str
    trial_id: str
    site_id: str
    status: str
    risk_score: float = Field(ge=0, le=1)
    enrolled_at: datetime
    synthetic: bool  # from participant_score.synthetic; always surfaced (dashboard.md)
    identity: ParticipantIdentity | None = None
    # SYNTHETIC baseline covariates; null unless a site role (same gate as identity). See BaselineContext.
    baseline: BaselineContext | None = None


class FactorContribution(BaseModel):
    feature: str
    contribution: float  # signed; sign encodes direction


class SuggestedAction(BaseModel):
    """An operational coordinator next-step (Phase 9 / Gate 9.3) — NOT clinical advice.

    A suggestion only: acting on it goes through the audited POST /interventions; ``intervention_kind``
    pre-fills that log. ``factor`` is the attribution driver it responds to (null = baseline).
    """

    action: str
    intervention_kind: str
    factor: str | None = None


class RiskExplanation(BaseModel):
    participant_id: str
    risk_score: float
    horizon_days: int = 28
    factors: list[FactorContribution]
    model_version: str
    # Operational protocol next-steps mapped from band + factors (suggestions; not clinical advice).
    recommended_actions: list[SuggestedAction] = []
    synthetic: bool = (
        True  # the actions are labelled synthetic where the risk is synthetic
    )


class RiskHistoryPoint(BaseModel):
    risk_score: float = Field(ge=0, le=1)
    risk_band: str
    model_version: str  # the champion-of-record model that produced THIS point
    model_card_ref: str
    synthetic: bool  # per-point provenance (B4); never smoothed away
    computed_at: datetime


class RiskHistory(BaseModel):
    participant_id: str
    points: list[RiskHistoryPoint]  # champion-only, ordered by computed_at ASC


class VisitIn(BaseModel):
    """One visit in the intake trajectory. Only ``attended`` is taken from the client; the
    missed-visit counters are DERIVED server-side and timestamps are server-anchored in the past
    (leakage-safe). Every persisted engagement row is ``synthetic=True``."""

    attended: bool


class ParticipantCreateIn(BaseModel):
    """INTAKE-1 request body. ``trial_id``/``site_id`` must be within the caller's scope (else
    403); the participant's ``sponsor_id`` is derived SERVER-SIDE from the trial, never from here."""

    coded_ref: str = Field(min_length=1, max_length=64)
    trial_id: str
    site_id: str
    enrolled_at: datetime | None = None
    age_years: float | None = None
    age_imputed: bool = False
    hba1c_pct: float | None = None
    hba1c_imputed: bool = False
    bmi: float | None = None
    bmi_imputed: bool = False
    sex: str | None = Field(default=None, max_length=16)
    arm_type: str | None = Field(default=None, max_length=64)
    # The visit trajectory the model scores. Empty → a minimal documented attended trajectory.
    visits: list[VisitIn] = Field(default_factory=list)


class ParticipantCreatedOut(BaseModel):
    participant_id: str
    coded_ref: str
    trial_id: str
    site_id: str
    enrolled_at: datetime
    synthetic: bool  # always True — intake is labeled-synthetic demo data
    n_visits: int
    scoring_job_id: (
        str  # the enqueued champion score (async; risk populates when the worker runs)
    )
    note: str = "scoring enqueued — risk score/band populate asynchronously"


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


def _require_site_role(scope: Scope) -> None:
    """Only site roles (coordinator / PI) may CREATE a participant — enrollment is a site action.

    Every non-site role (platform, auditor, sponsor-oversight, CRO) → 403, even when the requested
    trial is within their sponsor scope (the role gate is independent of the data-scope check).
    """
    if scope.role not in SITE_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="only site roles (coordinator/PI) may create participants",
        )


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("", status_code=201, response_model=ParticipantCreatedOut)
async def create_participant(
    body: ParticipantCreateIn,
    scope: Scope = ScopeDep,
) -> ParticipantCreatedOut:
    """POST /participants — the scoped intake front door (INTAKE-1).

    Creates a participant (coded_ref id + a synthetic visit trajectory) WITHIN the caller's scope,
    then ENQUEUES a champion score so the new participant appears scored without a manual batch
    trigger. SACRED: cross-tenant creation is impossible — the ``sponsor_id`` is derived server-side
    from the scoped trial, the write runs under RLS (``WITH CHECK`` backstop), and the caller's site
    scope is enforced (SEC-1). 403 for non-site roles and for any trial/site outside scope; 422 on a
    malformed body. Returns 201 promptly with the participant + the async scoring handle — the
    request never blocks on the model.
    """
    from vigil.services import participant_service, scoring_service

    _require_site_role(scope)
    try:
        view = participant_service.create_participant(
            scope,
            coded_ref=body.coded_ref,
            trial_id=body.trial_id,
            site_id=body.site_id,
            enrolled_at=body.enrolled_at,
            age_years=body.age_years,
            age_imputed=body.age_imputed,
            hba1c_pct=body.hba1c_pct,
            hba1c_imputed=body.hba1c_imputed,
            bmi=body.bmi,
            bmi_imputed=body.bmi_imputed,
            sex=body.sex,
            arm_type=body.arm_type,
            attended=[v.attended for v in body.visits],
        )
    except ScopeError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc
    except ValueError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)
        ) from exc

    # AUTO-SCORE: enqueue a scoped trial rescore — champion resolved at job time (NOT hardcoded),
    # reusing the SAME scope-validated enqueue every manual trigger uses. Async: the new
    # participant's risk_score/band populate when the worker runs (it then surfaces in the cohort /
    # at-risk views, and the existing crossing-detection path applies if it crosses high).
    job_id = await scoring_service.trigger_scoring(scope, trial_id=view.trial_id)
    return ParticipantCreatedOut(
        participant_id=view.participant_id,
        coded_ref=view.coded_ref,
        trial_id=view.trial_id,
        site_id=view.site_id,
        enrolled_at=view.enrolled_at,
        synthetic=view.synthetic,
        n_visits=view.n_visits,
        scoring_job_id=job_id,
    )


@router.get("/{participant_id}", response_model=ParticipantDetail)
async def get_participant(
    participant_id: str,
    scope: Scope = ScopeDep,
) -> ParticipantDetail:
    """GET /participants/{participant_id} — real, scope-filtered; 403 for platform roles.

    404 (fail closed) when the participant is out of scope / not found (RLS-hidden). Identity
    (coded_ref) is surfaced ONLY for site roles. ``synthetic`` comes from the champion score.
    """
    from vigil.services import participant_service

    _deny_platform(scope)
    view = participant_service.get_detail(scope, participant_id)
    if view is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="participant not found in scope",
        )
    is_identity = view.coded_ref is not None
    identity: ParticipantIdentity | None = (
        ParticipantIdentity(coded_ref=view.coded_ref) if is_identity else None
    )
    # Same gate as identity: baseline covariates surface only for site roles (None otherwise).
    baseline: BaselineContext | None = (
        BaselineContext(
            age_years=view.age_years,
            age_imputed=view.age_imputed,
            hba1c_pct=view.hba1c_pct,
            hba1c_imputed=view.hba1c_imputed,
            bmi=view.bmi,
            bmi_imputed=view.bmi_imputed,
            sex=view.sex,
            arm_type=view.arm_type,
        )
        if is_identity
        else None
    )
    return ParticipantDetail(
        participant_id=view.participant_id,
        trial_id=view.trial_id,
        site_id=view.site_id,
        status=view.status,
        risk_score=view.risk_score,
        enrolled_at=view.enrolled_at,
        synthetic=view.synthetic,
        identity=identity,
        baseline=baseline,
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
        recommended_actions=[
            SuggestedAction(
                action=a.action,
                intervention_kind=a.intervention_kind,
                factor=a.factor,
            )
            for a in view.recommended_actions
        ],
        synthetic=view.synthetic,
    )


@router.get("/{participant_id}/risk/history", response_model=RiskHistory)
async def get_risk_history(
    participant_id: str,
    scope: Scope = ScopeDep,
) -> RiskHistory:
    """GET /participants/{participant_id}/risk/history — champion risk trajectory (H2b).

    Promotion-aware semantic (b): each point is the row that was CHAMPION-OF-RECORD at its
    ``computed_at`` (reconstructed from the B3 promotion timeline), ordered ASC. A version
    change across the series is VISIBLE via each point's real ``model_version`` (not smoothed);
    shadow/challenger rows never appear. 403 for platform roles. Distinguishes security from
    data state: out-of-scope / not-found (RLS-hidden) → 404 fail-closed; an in-scope
    participant with no champion points yet → 200 with an empty ``points`` list.
    """
    from vigil.services import risk_service

    _deny_platform(scope)
    points = risk_service.get_participant_risk_history(scope, participant_id)
    if points is None:
        # Participant not visible to the caller (out of scope / not found) — fail closed.
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="participant not found in scope",
        )
    return RiskHistory(
        participant_id=participant_id,
        points=[
            RiskHistoryPoint(
                risk_score=p.risk_score,
                risk_band=p.risk_band,
                model_version=p.model_version,
                model_card_ref=p.model_card_ref,
                synthetic=p.synthetic,
                computed_at=p.computed_at,
            )
            for p in points
        ],
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
    """POST interventions — real + audited; actor is the authenticated user (server-side).

    403 for platform roles and auditor (write); 404 if the participant is out of scope.
    """
    from vigil.services import participant_service

    _deny_platform(scope)
    _deny_auditor_write(scope)
    view = participant_service.log_intervention(
        scope, participant_id, kind=body.kind, note=body.note
    )
    if view is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="participant not found in scope",
        )
    return InterventionOut(
        id=view.id,
        participant_id=view.participant_id,
        kind=view.kind,
        note=view.note,
        actor_user_id=view.actor_user_id,
        created_at=view.created_at,
    )


@router.get("/{participant_id}/interventions", response_model=Page)
async def list_interventions(
    participant_id: str,
    scope: Scope = ScopeDep,
) -> Page:
    """GET /participants/{participant_id}/interventions — real, scope-filtered; 403 platform."""
    from vigil.services import participant_service

    _deny_platform(scope)
    views = participant_service.list_interventions(scope, participant_id)
    if views is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail="participant not found in scope",
        )
    items = [
        InterventionOut(
            id=v.id,
            participant_id=v.participant_id,
            kind=v.kind,
            note=v.note,
            actor_user_id=v.actor_user_id,
            created_at=v.created_at,
        ).model_dump()
        for v in views
    ]
    return Page(items=items, next_cursor=None, total=len(items))
