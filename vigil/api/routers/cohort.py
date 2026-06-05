"""cohort router (api.md /cohort) — ranked, scope-filtered triage list (coded data only)."""

from __future__ import annotations

from datetime import datetime

from fastapi import APIRouter, HTTPException, Query, status
from pydantic import BaseModel, Field

from vigil.api.deps import ScopeDep
from vigil.core.schemas import Page
from vigil.core.scope import Scope, ScopeError
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


@router.get("", response_model=Page)
async def list_cohort(
    sponsor_id: str | None = Query(default=None),
    limit: int = Query(default=50, le=200),
    scope: Scope = ScopeDep,
) -> Page:
    try:
        rows = cohort_service.list_cohort(scope, sponsor_id=sponsor_id, limit=limit)
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
            updated_at=r.updated_at,
        ).model_dump()
        for r in rows
    ]
    return Page(items=items, next_cursor=None, total=len(items))
