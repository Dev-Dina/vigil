"""monitoring router (api.md /monitoring) — model routing administration.

Platform-scoped. The model-promotion endpoint is the manual, platform_admin-only path for
champion/challenger/shadow promotion (specs/routing.md § Audited promotion). Drift-triggered
fallback is NOT here — it is automatic and system-initiated (routing_service.handle_breach),
consuming an opaque breach signal whose delivery is deferred to the observability phase.
"""

from __future__ import annotations

from dataclasses import asdict
from datetime import datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from vigil.api.deps import ScopeDep
from vigil.core.schemas import Page
from vigil.core.scope import Scope
from vigil.services import monitoring_service, routing_service
from vigil.services.monitoring_service import MonitoringPermissionError
from vigil.services.routing_service import PromotionError, RoutingPermissionError

router = APIRouter(prefix="/monitoring", tags=["monitoring"])


# ---------------------------------------------------------------------------
# GET /monitoring/messages — observability inspect surface (platform/auditor only)
# ---------------------------------------------------------------------------


class MessageEventOut(BaseModel):
    """Mirrors specs/observability.md — ALREADY redacted; no raw-content field exists."""

    id: str
    conversation_id: str
    request_id: str
    sponsor_id: str | None
    role_or_guest_scope: str
    surface: str
    route_or_agent: str
    guardrail_decision: str
    status: str
    llm_provider_model: str
    latency_ms: int
    token_cost_estimate: float
    retrieved_chunks: list
    redacted_user_msg: str
    redacted_assistant_msg: str
    ts: datetime


@router.get("/messages", response_model=Page)
async def list_messages(
    scope: Scope = ScopeDep,
    surface: str | None = None,
    conversation_id: str | None = None,
    role_or_guest_scope: str | None = None,
    guardrail_decision: str | None = None,
    status_filter: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 50,
) -> Page:
    """GET /monitoring/messages — redacted message_events (PLATFORM/AUDITOR ONLY).

    403 for every sponsor/site role (role gate = primary guard). Runs under the caller's
    scope (RLS-bound = backstop). Returns only redacted fields (no raw column exists).
    """
    try:
        views = monitoring_service.list_message_events(
            scope,
            surface=surface,
            conversation_id=conversation_id,
            role_or_guest_scope=role_or_guest_scope,
            guardrail_decision=guardrail_decision,
            status=status_filter,
            since=since,
            until=until,
            limit=limit,
        )
    except MonitoringPermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc
    items = [
        MessageEventOut(
            id=v.id,
            conversation_id=v.conversation_id,
            request_id=v.request_id,
            sponsor_id=v.sponsor_id,
            role_or_guest_scope=v.role_or_guest_scope,
            surface=v.surface,
            route_or_agent=v.route_or_agent,
            guardrail_decision=v.guardrail_decision,
            status=v.status,
            llm_provider_model=v.llm_provider_model,
            latency_ms=v.latency_ms,
            token_cost_estimate=v.token_cost_estimate,
            retrieved_chunks=v.retrieved_chunks,
            redacted_user_msg=v.redacted_user_msg,
            redacted_assistant_msg=v.redacted_assistant_msg,
            ts=v.ts,
        ).model_dump()
        for v in views
    ]
    return Page(items=items, next_cursor=None, total=len(items))


# ---------------------------------------------------------------------------
# GET /monitoring/cost — token/cost rollups from REAL persisted usage
# ---------------------------------------------------------------------------


class CostRollupOut(BaseModel):
    surface: str
    llm_provider_model: str
    turns: int
    total_cost: float
    total_latency_ms: int
    avg_latency_ms: float


class CostReportOut(BaseModel):
    """Rollups over real persisted message_events usage — honest-zero, never fabricated."""

    total_turns: int
    total_cost: float
    rollups: list[CostRollupOut]


@router.get("/cost", response_model=CostReportOut)
async def get_cost(
    scope: Scope = ScopeDep,
    surface: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> CostReportOut:
    """GET /monitoring/cost — token/cost rollups (PLATFORM/AUDITOR ONLY; RLS-bound).

    Rollups come only from the real stored ``token_cost_estimate`` / ``latency_ms`` (honest-zero
    when usage was not captured, never faked). 403 for sponsor/site roles.
    """
    try:
        report = monitoring_service.cost_report(
            scope, surface=surface, since=since, until=until
        )
    except MonitoringPermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc
    return CostReportOut(
        total_turns=report.total_turns,
        total_cost=report.total_cost,
        rollups=[CostRollupOut(**asdict(r)) for r in report.rollups],
    )


# ---------------------------------------------------------------------------
# GET /monitoring/models — champion/challenger/shadow projection
# ---------------------------------------------------------------------------


class ModelStatusOut(BaseModel):
    model_name: str
    version: str
    role: str
    regime: str
    health: str
    promoted_at: datetime | None


@router.get("/models", response_model=Page)
async def list_models(scope: Scope = ScopeDep) -> Page:
    """GET /monitoring/models — current routing projection (PLATFORM/AUDITOR ONLY).

    Reflects routing_state (champion/challenger/shadow) as stored. 403 for sponsor/site roles.
    """
    try:
        views = monitoring_service.list_model_status(scope)
    except MonitoringPermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc
    items = [ModelStatusOut(**asdict(v)).model_dump() for v in views]
    return Page(items=items, next_cursor=None, total=len(items))


# ---------------------------------------------------------------------------
# GET /monitoring/drift — HONEST-EMPTY read surface (real drift DEFERRED)
# ---------------------------------------------------------------------------


class DriftPointOut(BaseModel):
    model_name: str
    metric: str
    value: float
    threshold: float
    breached: bool
    ts: datetime


@router.get("/drift", response_model=Page)
async def list_drift(scope: Scope = ScopeDep) -> Page:
    """GET /monitoring/drift — drift read surface (PLATFORM/AUDITOR ONLY).

    HONEST-EMPTY: drift is not computed/stored yet (real drift is a deferred TODO), so this
    returns an empty page rather than fabricating numbers. 403 for sponsor/site roles.
    """
    try:
        views = monitoring_service.list_drift_points(scope)
    except MonitoringPermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc
    items = [DriftPointOut(**asdict(v)).model_dump() for v in views]
    return Page(items=items, next_cursor=None, total=len(items))


class ModelPromoteIn(BaseModel):
    regime: str
    model_version: str
    model_card_ref: str = Field(min_length=1)
    eval_provenance: str = Field(min_length=1)
    reason: str = "manual_promotion"


class ModelPromoteOut(BaseModel):
    regime: str
    from_version: str | None
    to_version: str
    actor_user_id: str
    audit_id: str


@router.post(
    "/models/promote",
    response_model=ModelPromoteOut,
    status_code=status.HTTP_201_CREATED,
)
async def promote_model(
    body: ModelPromoteIn, scope: Scope = ScopeDep
) -> ModelPromoteOut:
    """POST /monitoring/models/promote — manual champion promotion (platform_admin only).

    403 for any non-platform_admin caller; 400 on honesty/safety violations.
    """
    try:
        result = routing_service.promote(
            scope,
            regime=body.regime,
            model_version=body.model_version,
            model_card_ref=body.model_card_ref,
            eval_provenance=body.eval_provenance,
            reason=body.reason,
        )
    except RoutingPermissionError as exc:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN, detail=str(exc)
        ) from exc
    except PromotionError as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)
        ) from exc
    return ModelPromoteOut(
        regime=result.regime,
        from_version=result.from_version,
        to_version=result.to_version,
        actor_user_id=result.actor_user_id,
        audit_id=result.audit_id,
    )
