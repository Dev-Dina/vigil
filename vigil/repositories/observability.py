"""Observability repository — the typed write path for ``message_events``.

The ONLY sanctioned way to persist a chatbot/assistant turn (specs/observability.md). Runs
under the caller's RLS-scoped session; the ``message_events_scope`` policy (cross-tenant-by-role,
like audit_log) decides visibility. This module never adds a WHERE sponsor_id clause.

WRITE-PATH CONTRACT (Gate 5.1): ``redacted_user_msg`` / ``redacted_assistant_msg`` arrive
ALREADY REDACTED. Redaction LOGIC (fail-loud, before the LLM) is Gate 5.2 — it is NOT done here.
This function persists a typed row; it calls no LLM and no redactor. There is no raw-content
parameter, so raw text cannot be stored through this path by construction.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from vigil.db.models import MessageEvent


def write_message_event(
    session: Session,
    *,
    conversation_id: uuid.UUID,
    request_id: str,
    sponsor_id: uuid.UUID | None,
    role_or_guest_scope: str,
    surface: str,
    guardrail_decision: str,
    status: str,
    redacted_user_msg: str = "",
    redacted_assistant_msg: str = "",
    route_or_agent: str = "",
    retrieved_chunks: list[Any] | None = None,
    llm_provider_model: str = "",
    latency_ms: int = 0,
    token_cost_estimate: float = 0.0,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
) -> MessageEvent:
    """Append one redacted ``message_events`` row for a turn (specs/observability.md).

    ``sponsor_id=None`` for Guide/platform turns (null-sponsor, platform-visible only). All
    message text MUST already be redacted (contract above). Runs under the caller's RLS session.
    ``prompt_tokens`` / ``completion_tokens`` are the REAL per-turn counts (Gate COST-1), honest-
    zero when no generation call was made.
    """
    row = MessageEvent(
        conversation_id=conversation_id,
        request_id=request_id,
        sponsor_id=sponsor_id,
        role_or_guest_scope=role_or_guest_scope,
        surface=surface,
        route_or_agent=route_or_agent,
        guardrail_decision=guardrail_decision,
        retrieved_chunks=retrieved_chunks if retrieved_chunks is not None else [],
        llm_provider_model=llm_provider_model,
        latency_ms=latency_ms,
        token_cost_estimate=token_cost_estimate,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        status=status,
        redacted_user_msg=redacted_user_msg,
        redacted_assistant_msg=redacted_assistant_msg,
    )
    session.add(row)
    session.flush()
    return row


def query_message_events(
    session: Session,
    *,
    surface: str | None = None,
    conversation_id: uuid.UUID | None = None,
    role_or_guest_scope: str | None = None,
    guardrail_decision: str | None = None,
    status: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
    limit: int = 50,
) -> list[MessageEvent]:
    """Read message_events under the caller's RLS-scoped session (newest-first).

    Visibility is the ``message_events_scope`` RLS policy (cross-tenant-by-role) — this never
    adds a sponsor_id WHERE clause; filters are content/metadata only. Returns ORM rows carrying
    only redacted text (no raw column exists).
    """
    stmt = select(MessageEvent)
    if surface is not None:
        stmt = stmt.where(MessageEvent.surface == surface)
    if conversation_id is not None:
        stmt = stmt.where(MessageEvent.conversation_id == conversation_id)
    if role_or_guest_scope is not None:
        stmt = stmt.where(MessageEvent.role_or_guest_scope == role_or_guest_scope)
    if guardrail_decision is not None:
        stmt = stmt.where(MessageEvent.guardrail_decision == guardrail_decision)
    if status is not None:
        stmt = stmt.where(MessageEvent.status == status)
    if since is not None:
        stmt = stmt.where(MessageEvent.ts >= since)
    if until is not None:
        stmt = stmt.where(MessageEvent.ts < until)
    stmt = stmt.order_by(MessageEvent.ts.desc()).limit(limit)
    return list(session.execute(stmt).scalars().all())


def cost_rollup(
    session: Session,
    *,
    surface: str | None = None,
    since: datetime | None = None,
    until: datetime | None = None,
) -> list[tuple[str, str, int, float, int, int, int]]:
    """Aggregate REAL persisted usage from ``message_events``, grouped by surface + model.

    Returns ``(surface, llm_provider_model, turns, total_cost, total_latency_ms, prompt_tokens,
    completion_tokens)`` tuples, summed straight from the stored columns — NO fabrication: a turn
    that captured honest-zero usage contributes zero. Runs under the caller's RLS-scoped session
    (visibility = the ``message_events_scope`` policy); never adds a sponsor_id WHERE clause. Empty
    result when no events match (honest-empty).
    """
    stmt = select(
        MessageEvent.surface,
        MessageEvent.llm_provider_model,
        func.count().label("turns"),
        func.coalesce(func.sum(MessageEvent.token_cost_estimate), 0).label(
            "total_cost"
        ),
        func.coalesce(func.sum(MessageEvent.latency_ms), 0).label("total_latency_ms"),
        func.coalesce(func.sum(MessageEvent.prompt_tokens), 0).label("prompt_tokens"),
        func.coalesce(func.sum(MessageEvent.completion_tokens), 0).label(
            "completion_tokens"
        ),
    )
    if surface is not None:
        stmt = stmt.where(MessageEvent.surface == surface)
    if since is not None:
        stmt = stmt.where(MessageEvent.ts >= since)
    if until is not None:
        stmt = stmt.where(MessageEvent.ts < until)
    stmt = stmt.group_by(
        MessageEvent.surface, MessageEvent.llm_provider_model
    ).order_by(MessageEvent.surface, MessageEvent.llm_provider_model)
    return [
        (
            surface_,
            model or "",
            int(turns),
            float(cost),
            int(latency),
            int(prompt_tok),
            int(completion_tok),
        )
        for surface_, model, turns, cost, latency, prompt_tok, completion_tok in session.execute(
            stmt
        ).all()
    ]
