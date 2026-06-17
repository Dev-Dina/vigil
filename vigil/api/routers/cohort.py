"""cohort router (api.md /cohort) — ranked, scope-filtered triage list (coded only)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from vigil.api.deps import ScopeDep
from vigil.core.schemas import Page
from vigil.core.scope import Scope, ScopeError
from vigil.domain import PLATFORM_ROLES
from vigil.services import cohort_service

router = APIRouter(prefix="/cohort", tags=["cohort"])


class CohortRow(BaseModel):
    participant_id: str
    trial_id: str
    site_id: str
    risk_score: float = Field(ge=0, le=1)
    risk_band: str
    top_factors: list[str] = []
    updated_at: datetime
    synthetic: bool


@router.get("", response_model=Page)
async def list_cohort(
    sponsor_id: str | None = Query(default=None),
    limit: int = Query(default=50, le=200),
    risk_band: str | None = Query(default=None, pattern="^(high|medium|low)$"),
    sort: str = Query(default="risk_desc", pattern="^(risk_desc|risk_asc)$"),
    scope: Scope = ScopeDep,
) -> Page:
    # Platform users (ML admin, auditor) have no sponsor scope; participant-level
    # access is explicitly forbidden (specs/scoring.md, specs/domain.md).
    if scope.role in PLATFORM_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="platform roles may not access participant cohort data",
        )
    try:
        # Phase-9 at-risk surface = /cohort?risk_band=high&sort=risk_desc, scope-bound (SEC-1).
        rows = cohort_service.list_cohort(
            scope,
            sponsor_id=sponsor_id,
            limit=limit,
            risk_band=risk_band,
            sort=sort,
        )
    except ScopeError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc
    items = [
        CohortRow(
            participant_id=r.participant_id,
            trial_id=r.trial_id,
            site_id=r.site_id,
            risk_score=r.risk_score,
            risk_band=r.risk_band,
            top_factors=r.top_factors,
            updated_at=r.updated_at,
            synthetic=r.synthetic,
        ).model_dump()
        for r in rows
    ]
    return Page(items=items, next_cursor=None, total=len(items))
