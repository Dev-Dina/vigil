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
    participant_id: str  # internal UUID locator
    coded_ref: str | None = (
        None  # human-readable pseudonymous ref; identity roles only (null else)
    )
    trial_id: str
    site_id: str
    risk_score: float = Field(ge=0, le=1)
    risk_band: str
    top_factors: list[str] = []
    updated_at: datetime
    enrolled_at: datetime
    synthetic: bool


class CohortSummary(BaseModel):
    total: int
    by_band: dict[str, int]  # {"high","medium","low"} → count (api.md § cohort)
    mean_risk: float


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
            coded_ref=r.coded_ref,
            trial_id=r.trial_id,
            site_id=r.site_id,
            risk_score=r.risk_score,
            risk_band=r.risk_band,
            top_factors=r.top_factors,
            updated_at=r.updated_at,
            enrolled_at=r.enrolled_at,
            synthetic=r.synthetic,
        ).model_dump()
        for r in rows
    ]
    return Page(items=items, next_cursor=None, total=len(items))


@router.get("/summary", response_model=CohortSummary)
async def cohort_summary(
    sponsor_id: str | None = Query(default=None),
    trial_id: str | None = Query(default=None),
    site_id: str | None = Query(default=None),
    scope: Scope = ScopeDep,
) -> CohortSummary:
    """Counts + mean risk for the caller's scoped cohort (api.md § cohort / CohortSummary).

    Same scope-binding as ``GET /cohort`` (RLS + SEC-1 ``scope_filter`` narrowing): the summary
    covers ONLY the caller's scoped slice — a coordinator's totals reflect their site only;
    cross-tenant + cross-site hold exactly as for the list. Platform roles are forbidden.
    """
    if scope.role in PLATFORM_ROLES:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="platform roles may not access participant cohort data",
        )
    try:
        view = cohort_service.summarize_cohort(
            scope, sponsor_id=sponsor_id, trial_id=trial_id, site_id=site_id
        )
    except ScopeError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc
    return CohortSummary(
        total=view.total, by_band=view.by_band, mean_risk=view.mean_risk
    )
