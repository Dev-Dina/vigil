"""Scoring router (specs/scoring.md).

POST /scoring/trigger    — 202 Accepted; never inline
GET  /scoring/jobs/{id}  — poll completion
POST /scoring/inject_events — demo-mode only; 404 in production
"""

from __future__ import annotations

from pydantic import BaseModel
from fastapi import APIRouter, HTTPException, status

from vigil.api.deps import ScopeDep
from vigil.core.config import get_settings
from vigil.core.errors import ServiceUnavailableError
from vigil.core.scope import Scope, ScopeError
from vigil.domain import Role
from vigil.services import scoring_service
from vigil.services.scoring_service import JobAccepted, ScoringJobStatus

router = APIRouter(prefix="/scoring", tags=["scoring"])


# ---------------------------------------------------------------------------
# Request schemas
# ---------------------------------------------------------------------------


class ScoringTriggerIn(BaseModel):
    trial_id: str
    model_version: str | None = None


class InjectEventsIn(BaseModel):
    trial_id: str
    events: list[dict]
    demo_mode_key: str  # validated against Vault-held demo secret; hard error if wrong


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@router.post("/trigger", status_code=202, response_model=JobAccepted)
async def trigger(body: ScoringTriggerIn, scope: Scope = ScopeDep) -> JobAccepted:
    """Enqueue score_trial; return 202 immediately. Never inline."""
    if scope.role == Role.AUDITOR:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="auditor may not trigger scoring",
        )
    try:
        job_id = await scoring_service.trigger_scoring(
            scope, trial_id=body.trial_id, model_version=body.model_version
        )
    except ScopeError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc
    return JobAccepted(job_id=job_id, trial_id=body.trial_id)


@router.get("/jobs/{job_id}", response_model=ScoringJobStatus)
async def job_status(job_id: str, scope: Scope = ScopeDep) -> ScoringJobStatus:
    """Poll job completion. Any authenticated user may call this.

    A Redis/job-store outage surfaces as 503 (honest "temporarily unavailable"), never a 404.
    """
    try:
        result = await scoring_service.get_job_status(job_id)
    except ServiceUnavailableError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)
        ) from exc
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="job not found"
        )
    return result


@router.post("/inject_events", status_code=204)
async def inject_events(body: InjectEventsIn, scope: Scope = ScopeDep) -> None:
    """Inject synthetic engagement events — demo mode only; 404 in production."""
    settings = get_settings()
    if not settings.demo_mode:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="not found")

    if body.demo_mode_key != settings.demo_secret:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="invalid demo_mode_key",
        )

    # Validate scope — caller must have access to the trial.
    if not scope.is_platform:
        matched = any(
            t.trial_id is None or t.trial_id == body.trial_id for t in scope.tuples
        )
        if not matched:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="trial not within your scope",
            )

    # Events are accepted; enqueue the real scoring pipeline.
    # (In a full implementation, events would be persisted first.)
    try:
        await scoring_service.trigger_scoring(scope, trial_id=body.trial_id)
    except ScopeError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc
