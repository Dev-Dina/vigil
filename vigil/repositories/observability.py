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
from typing import Any

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
) -> MessageEvent:
    """Append one redacted ``message_events`` row for a turn (specs/observability.md).

    ``sponsor_id=None`` for Guide/platform turns (null-sponsor, platform-visible only). All
    message text MUST already be redacted (contract above). Runs under the caller's RLS session.
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
        status=status,
        redacted_user_msg=redacted_user_msg,
        redacted_assistant_msg=redacted_assistant_msg,
    )
    session.add(row)
    session.flush()
    return row
