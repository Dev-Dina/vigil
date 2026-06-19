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
from vigil.domain import Capability, can_do
from vigil.repositories import drift as drift_repo
from vigil.repositories import observability as obs_repo
from vigil.repositories import routing as routing_repo
from vigil.repositories.session import platform_session, scoped_session


def _require(scope: Scope, capability: Capability, what: str) -> None:
    """Capability gate for a monitoring surface (Gate RBAC-OPS; else 403).

    MLOps reads (drift/registry/model-traffic) require ``MLOPS_READ`` (platform_admin, auditor,
    mlops_engineer); LLMOps reads (cost/messages) require ``LLMOPS_READ`` (platform_admin,
    auditor, llmops_engineer); the drift-run ACTION requires ``MLOPS_WRITE`` (platform_admin,
    mlops_engineer). platform_admin/auditor keep full read; every sponsor/site/CRO role is denied.
    """
    if not can_do(scope.role, capability):
        raise MonitoringPermissionError(
            f"monitoring {what} denied: role {scope.role!r} lacks {capability.value} "
            "(specs/domain.md § Operational roles; api.md § monitoring)"
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
    _require(scope, Capability.LLMOPS_READ, "inspect")

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
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


@dataclass(frozen=True, slots=True)
class CostReportView:
    total_turns: int
    total_cost: float
    total_prompt_tokens: int
    total_completion_tokens: int
    total_tokens: int
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
    _require(scope, Capability.LLMOPS_READ, "cost")
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
            prompt_tokens=prompt_tok,
            completion_tokens=completion_tok,
            total_tokens=prompt_tok + completion_tok,
        )
        for s, model, turns, total_cost, total_latency, prompt_tok, completion_tok in rows
    ]
    return CostReportView(
        total_turns=sum(r.turns for r in rollups),
        total_cost=round(sum(r.total_cost for r in rollups), 6),
        total_prompt_tokens=sum(r.prompt_tokens for r in rollups),
        total_completion_tokens=sum(r.completion_tokens for r in rollups),
        total_tokens=sum(r.total_tokens for r in rollups),
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
    _require(scope, Capability.MLOPS_READ, "models")
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
    model_name: str  # the champion model_version the distribution came from
    distribution: str  # what was compared (carries a provenance suffix on a demo)
    metric: str  # 'psi' | 'ks'
    value: float
    threshold: float
    breached: bool
    reference_n: int
    current_n: int
    synthetic: bool  # the cohort the distribution came from is synthetic
    constructed_demo: (
        bool  # the current window was a constructed shift (NOT observed drift)
    )
    note: str
    ts: datetime


def list_drift_points(scope: Scope, *, limit: int = 50) -> list[DriftPointView]:
    """Drift read surface — PLATFORM/AUDITOR ONLY. Returns the REAL computed PSI/KS points.

    Backed by the Gate M1 producer (``compute_drift`` job → ``drift_metric``): each point is a real
    PSI or KS value over the champion score distribution. Empty list when no run has computed any
    points yet (honest-empty by data state, never a fabricated number). Provenance travels with
    every point — ``synthetic`` (synthetic cohort) and ``constructed_demo`` (a deliberately-shifted
    demonstration of the detector, not observed drift). ``drift_metric`` is a platform-tier table
    (no tenant data); read under ``platform_session`` (the role gate above is the access guard).
    """
    _require(scope, Capability.MLOPS_READ, "drift")
    with platform_session() as session:
        rows = drift_repo.list_recent_drift_points(session, limit=limit)
        return [
            DriftPointView(
                model_name=r.model_version,
                distribution=r.distribution,
                metric=r.metric,
                value=r.value,
                threshold=r.threshold,
                breached=r.breached,
                reference_n=r.reference_n,
                current_n=r.current_n,
                synthetic=r.synthetic,
                constructed_demo=r.constructed_demo,
                note=r.note,
                ts=r.computed_at,
            )
            for r in rows
        ]


async def trigger_drift_run(
    scope: Scope, *, regime: str = "t2d", demo_shift: float = 0.0
) -> str:
    """Enqueue the drift producer (Gate M1) — MLOPS_WRITE only. Returns the job id.

    The on-demand path mirroring the scheduled cron run. ``demo_shift != 0`` enqueues a CONSTRUCTED
    breach demonstration (labelled in the persisted points), never presented as observed drift.
    MLOPS_WRITE only (platform_admin + mlops_engineer); auditor (read-only) and llmops_engineer are
    denied — triggering a job is a model-lifecycle action, not a read.
    """
    from vigil.workers.settings import enqueue

    _require(scope, Capability.MLOPS_WRITE, "drift run trigger")
    return await enqueue("compute_drift", regime=regime, demo_shift=demo_shift)
