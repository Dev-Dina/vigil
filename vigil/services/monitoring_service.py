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
from vigil.repositories.session import scoped_session


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
    if scope.role not in PLATFORM_ROLES:
        raise MonitoringPermissionError(
            f"monitoring inspect denied: role {scope.role!r} is not platform/auditor "
            "(specs/observability.md § Inspect endpoint scope contract)"
        )

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
