"""Assistant router (api.md § assistant) — the in-app, scope-bound chatbot.

NOT the public Guide. Slow LLM/agent work is enqueued (Arq): POST messages returns 202 + a
job_id; the answer is polled. Every turn writes a redacted ``message_events`` row (in the job).
Scope is injected as a dependency and re-resolved in the job (5.4); the client never asserts scope.
"""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, Field

from vigil.api.deps import ScopeDep
from vigil.core.logging import request_id_var
from vigil.core.scope import Scope
from vigil.services import assistant_service
from vigil.services.assistant_service import (
    AssistantTurn,
    ConversationOut,
    JobAccepted,
    JobPending,
)

router = APIRouter(prefix="/assistant", tags=["assistant"])


class AssistantMessageIn(BaseModel):
    content: str = Field(max_length=8000)
    participant_id: str | None = (
        None  # if set, must be within scope; grounds the answer
    )


@router.post("/conversations", response_model=ConversationOut)
async def open_conversation(scope: Scope = ScopeDep) -> ConversationOut:
    """Open a new conversation id."""
    return assistant_service.open_conversation()


@router.post(
    "/conversations/{conversation_id}/messages",
    status_code=202,
    response_model=JobAccepted,
)
async def post_message(
    conversation_id: str,
    body: AssistantMessageIn,
    scope: Scope = ScopeDep,
) -> JobAccepted:
    """Enqueue an assistant turn (202). Never runs the agent inline."""
    return await assistant_service.post_message(
        scope,
        conversation_id=conversation_id,
        content=body.content,
        participant_id=body.participant_id,
        request_id=request_id_var.get() or "",
    )


@router.get("/conversations/{conversation_id}/messages")
async def list_messages(
    conversation_id: str, scope: Scope = ScopeDep
) -> list[AssistantTurn]:
    """Redacted transcript for a conversation (RLS-scoped)."""
    return assistant_service.list_messages(scope, conversation_id)


@router.get("/jobs/{job_id}")
async def get_job(job_id: str, scope: Scope = ScopeDep) -> AssistantTurn | JobPending:
    """Poll an enqueued turn: the finished AssistantTurn, or JobPending; 404 if unknown."""
    result = await assistant_service.get_job(job_id)
    if result is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail="job not found"
        )
    return result
