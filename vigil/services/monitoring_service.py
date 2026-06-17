"""Monitoring read services (observability inspect surface, specs/observability.md § Phase 6).

GET /monitoring/messages: the per-turn audit inspect surface. PLATFORM/AUDITOR ONLY (the role
gate is the primary guard) AND RLS-bound (the message_events_scope policy is the backstop — the
query runs under ``scoped_session(scope)``, never ``platform_session`` unconditionally, so it can
only honour the cross-tenant-by-role boundary, never widen it). Returns ONLY redacted fields;
there is no raw-content column to reach.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from vigil.core.scope import Scope
from vigil.domain import PLATFORM_ROLES
from vigil.repositories import observability as obs_repo
from vigil.repositories import routing as routing_repo
from vigil.repositories.session import platform_session, scoped_session


def _require_platform(scope: Scope, what: str) -> None:
    """Role gate shared by every monitoring read: platform/auditor only (else 403)."""
    if scope.role not in PLATFORM_ROLES:
        raise MonitoringPermissionError(
            f"monitoring {what} denied: role {scope.role!r} is not platform/auditor "
            "(specs/observability.md § Phase 6 contracts; api.md § monitoring)"
        )


class MonitoringPermissionError(PermissionError):
    """Raised when a non-platform/auditor scope attempts a monitoring read (→ 403)."""


@dataclass(frozen=True, slots=True)
class MessageEventView:
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


def list_message_events(
    scope: Scope,
    *,
    surface: str | None = None,
    conversation_id: str | None = None,
    role_or_guest_scope: str | None = None,
    guardrail_decision: str | None = None,
    status: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 50,
) -> list[MessageEventView]:
    """Redacted message_events for the inspect page. Platform/auditor only; RLS-bound.

    Role gate (primary): non-platform/auditor → ``MonitoringPermissionError`` (router → 403).
    RLS backstop: the read runs under ``scoped_session(scope)`` — a platform/auditor scope has
    ``is_platform=on`` so RLS admits all sponsors (the legitimate cross-tenant-by-role read); any
    other scope would be narrowed by RLS to its own sponsor (defence in depth).
    """
    _require_platform(scope, "inspect")

    conv = uuid.UUID(conversation_id) if conversation_id else None

    with scoped_session(
        scope
    ) as session:  # RLS-bound; NEVER platform_session unconditionally
        rows = obs_repo.query_message_events(
            session,
            surface=surface,
            conversation_id=conv,
            role_or_guest_scope=role_or_guest_scope,
            guardrail_decision=guardrail_decision,
            status=status,
            since=since,
            until=until,
            limit=limit,
        )
        return [
            MessageEventView(
                id=str(r.id),
                conversation_id=str(r.conversation_id),
                request_id=r.request_id,
                sponsor_id=str(r.sponsor_id) if r.sponsor_id is not None else None,
                role_or_guest_scope=r.role_or_guest_scope,
                surface=r.surface,
                route_or_agent=r.route_or_agent,
                guardrail_decision=r.guardrail_decision,
                status=r.status,
                llm_provider_model=r.llm_provider_model,
                latency_ms=r.latency_ms,
                token_cost_estimate=float(r.token_cost_estimate),
                retrieved_chunks=list(r.retrieved_chunks),
                redacted_user_msg=r.redacted_user_msg,
                redacted_assistant_msg=r.redacted_assistant_msg,
                ts=r.ts,
            )
            for r in rows
        ]


# ---------------------------------------------------------------------------
# GET /monitoring/cost — token/cost rollups from REAL persisted usage
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class CostRollupView:
    surface: str
    llm_provider_model: str
    turns: int
    total_cost: float
    total_latency_ms: int
    avg_latency_ms: float


@dataclass(frozen=True, slots=True)
class CostReportView:
    total_turns: int
    total_cost: float
    rollups: list[CostRollupView]


def cost_report(
    scope: Scope,
    *,
    surface: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> CostReportView:
    """Token/cost rollups over ``message_events`` (PLATFORM/AUDITOR ONLY; RLS-bound).

    Aggregates ONLY the real persisted ``token_cost_estimate`` / ``latency_ms`` — honest-zero
    when usage was never captured, NEVER a fabricated cost figure (specs/observability.md §
    Cost capture). Runs under ``scoped_session(scope)`` so the message_events RLS is the
    cross-tenant-by-role boundary (platform/auditor sees across; any other scope is narrowed).
    """
    _require_platform(scope, "cost")
    with scoped_session(scope) as session:
        rows = obs_repo.cost_rollup(session, surface=surface, since=since, until=until)
    rollups = [
        CostRollupView(
            surface=s,
            llm_provider_model=model,
            turns=turns,
            total_cost=round(total_cost, 6),
            total_latency_ms=total_latency,
            avg_latency_ms=round(total_latency / turns, 2) if turns else 0.0,
        )
        for s, model, turns, total_cost, total_latency in rows
    ]
    return CostReportView(
        total_turns=sum(r.turns for r in rollups),
        total_cost=round(sum(r.total_cost for r in rollups), 6),
        rollups=rollups,
    )


# ---------------------------------------------------------------------------
# GET /monitoring/models — champion/challenger/shadow projection (routing_state)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class ModelStatusView:
    model_name: str
    version: str
    role: str
    regime: str
    health: str
    promoted_at: datetime | None


def list_model_status(scope: Scope) -> list[ModelStatusView]:
    """Current routing projection (champion/challenger/shadow) — PLATFORM/AUDITOR ONLY.

    Reflects ``routing_state`` exactly as stored. ``routing_state`` is a global infra table with
    NO RLS, so (like routing_service) the platform/auditor gate is the sole guard and the read
    runs under ``platform_session``. ``model_name`` surfaces the regime (the model's identity in
    storage — there is no separate name column); ``version`` is the stored ``model_version``.
    """
    _require_platform(scope, "models")
    with platform_session() as session:
        rows = routing_repo.list_routing_state(session)
    return [
        ModelStatusView(
            model_name=r.regime,
            version=r.model_version,
            role=r.role,
            regime=r.regime,
            health=r.health,
            promoted_at=r.promoted_at,
        )
        for r in rows
    ]


# ---------------------------------------------------------------------------
# GET /monitoring/drift — HONEST-EMPTY read surface (real drift DEFERRED)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class DriftPointView:
    model_name: str
    metric: str
    value: float
    threshold: float
    breached: bool
    ts: datetime


def list_drift_points(scope: Scope) -> list[DriftPointView]:
    """Drift read surface — PLATFORM/AUDITOR ONLY; HONEST-EMPTY (always ``[]`` today).

    Drift is NOT computed or stored anywhere yet — B3 deferred the drift SOURCE to a future
    phase; routing only CONSUMES an opaque breach signal (routing_service.handle_breach). This
    is the read surface only: it returns an empty list rather than fabricating drift numbers
    (specs/observability.md § Drift read-surface only).

    TODO(drift): real drift computation (rolling PSI / AUC / prediction-drift writing drift rows
    + this surface reading them) is DEFERRED future work, NOT Phase 6. Until that exists this
    function is honestly empty by construction.
    """
    _require_platform(scope, "drift")
    return []
