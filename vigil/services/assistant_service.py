"""Assistant service — enqueue/poll assistant turns + redacted transcript (api.md § assistant).

The request path NEVER runs the LLM/agent inline: ``post_message`` enqueues ``run_assistant_turn``
(202 + job_id) and ``get_job`` polls the Arq result. The turn job re-resolves the caller's FULL
scope from user_id (5.4) and writes a redacted ``message_events`` row (5.1). The transcript reads
those redacted rows under the caller's RLS scope — raw text never exists.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Literal

from pydantic import BaseModel
from sqlalchemy import select

from vigil.core.scope import Scope
from vigil.db.models import MessageEvent
from vigil.repositories.session import scoped_session
from vigil.workers.settings import enqueue


# ---------------------------------------------------------------------------
# Schemas (mirror api.md § assistant)
# ---------------------------------------------------------------------------


class ConversationOut(BaseModel):
    conversation_id: str


class JobAccepted(BaseModel):
    job_id: str
    conversation_id: str
    status: Literal["queued"] = "queued"


class JobPending(BaseModel):
    job_id: str
    status: Literal["queued", "running"]


class AssistantTurn(BaseModel):
    conversation_id: str
    role: Literal["user", "assistant"]
    content: str  # redacted at rest
    guardrail_decision: Literal["allowed", "blocked"]
    created_at: datetime


# ---------------------------------------------------------------------------
# Service functions
# ---------------------------------------------------------------------------


def open_conversation() -> ConversationOut:
    """Open a new conversation id (no row until the first message turn is logged)."""
    return ConversationOut(conversation_id=str(uuid.uuid4()))


async def post_message(
    scope: Scope,
    *,
    conversation_id: str,
    content: str,
    participant_id: str | None = None,
    request_id: str = "",
) -> JobAccepted:
    """Enqueue an assistant turn; return 202 handle. Never runs the agent inline.

    The job carries ``user_id`` (NOT a serialized scope) so the turn re-resolves the full scope at
    job time. The caller's single bound sponsor is passed for RLS binding (None for platform).
    """
    bound_sponsor = next(iter(scope.sponsor_ids), None)
    job_id = await enqueue(
        "run_assistant_turn",
        conversation_id=conversation_id,
        user_id=scope.user_id,
        content=content,
        participant_id=participant_id,
        sponsor_id=bound_sponsor,
        request_id=request_id,
    )
    return JobAccepted(job_id=job_id, conversation_id=conversation_id)


async def get_job(job_id: str) -> AssistantTurn | JobPending | None:
    """Poll an assistant turn job → the finished AssistantTurn, JobPending, or None if unknown."""
    from arq import create_pool
    from arq.jobs import Job, JobStatus

    from vigil.workers.settings import _redis_settings

    try:
        pool = await create_pool(_redis_settings())
    except Exception:  # noqa: BLE001 - redis unreachable → treat as unknown
        return None
    try:
        job = Job(job_id, pool)
        raw_status = await job.status()
        if raw_status == JobStatus.not_found:
            return None
        if raw_status != JobStatus.complete:
            mapped = "running" if raw_status == JobStatus.in_progress else "queued"
            return JobPending(job_id=job_id, status=mapped)  # type: ignore[arg-type]
        info = await job.result_info()
        if info is None or not info.success or not isinstance(info.result, dict):
            return None
        return _turn_from_result(info.result)
    finally:
        await pool.aclose()


def _turn_from_result(result: dict) -> AssistantTurn:
    return AssistantTurn(
        conversation_id=result["conversation_id"],
        role="assistant",
        content=result["content"],
        guardrail_decision=result["guardrail_decision"],
        created_at=datetime.fromisoformat(result["created_at"]),
    )


def list_messages(scope: Scope, conversation_id: str) -> list[AssistantTurn]:
    """Redacted transcript for a conversation, under the caller's RLS scope (message_events).

    Each turn row expands to a user turn (redacted inbound) + an assistant turn (redacted
    response), ordered by event time. RLS hides other tenants' rows; the conversation_id scopes it.
    """
    try:
        conv = uuid.UUID(conversation_id)
    except (ValueError, AttributeError):
        return []
    with scoped_session(scope) as session:
        rows = (
            session.execute(
                select(MessageEvent)
                .where(MessageEvent.conversation_id == conv)
                .order_by(MessageEvent.ts.asc())
            )
            .scalars()
            .all()
        )
    turns: list[AssistantTurn] = []
    for r in rows:
        ts = r.ts if r.ts is not None else datetime.now(tz=timezone.utc)
        if r.redacted_user_msg:
            turns.append(
                AssistantTurn(
                    conversation_id=conversation_id,
                    role="user",
                    content=r.redacted_user_msg,
                    guardrail_decision=r.guardrail_decision,  # type: ignore[arg-type]
                    created_at=ts,
                )
            )
        turns.append(
            AssistantTurn(
                conversation_id=conversation_id,
                role="assistant",
                content=r.redacted_assistant_msg,
                guardrail_decision=r.guardrail_decision,  # type: ignore[arg-type]
                created_at=ts,
            )
        )
    return turns
