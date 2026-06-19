"""One Guide turn: guardrail → retrieve/answer/refuse → redacted event row (specs/isolation.md §).

The orchestrator for the public surface. EVERY outcome — a guardrail block, a relevance refusal,
or a grounded answer — writes ONE redacted row to the Guide's OWN ``message_events`` sink, so
guardrail firing is provable/auditable. Redacted-only (there is no raw column). The event write is
best-effort: a sink failure is swallowed and never breaks the turn (the answer/refusal still
returns). Imports nothing from `vigil.*`.

Outcome encoding (mirrors the app):
- guardrail-blocked content → ``guardrail_decision="blocked"``, ``status="refused"``;
- no grounded answer (relevance/zero-retrieval refusal) → ``"allowed"`` + ``"refused"`` (it passed
  the guardrails, it simply had nothing to ground in);
- grounded answer → ``"allowed"`` + ``"ok"``.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Engine
from sqlalchemy.orm import Session

from guide.guardrails import guard_inbound
from guide.observability import write_message_event
from guide.rag import answer_question
from guide.redaction import redact
from guide.retrieval import IndexedChunk

_BLOCKED_MSG = (
    "I can't help with that. I'm a public guide that only explains the Vigil project from "
    "approved materials — I don't give medical, security, or out-of-scope answers."
)
# A fixed, terse refusal for individual/participant-data asks — the Guide is public and has no
# access to any real participant; they are HARD-blocked before retrieval or the LLM.
_PARTICIPANT_MSG = (
    "Participant information is restricted. The Guide only answers general questions about Vigil."
)


@dataclass(frozen=True, slots=True)
class TurnResult:
    content: str
    citations: list[dict] = field(default_factory=list)
    guardrail_decision: str = "allowed"  # allowed | blocked
    status: str = "ok"  # ok | refused
    route_or_agent: str = ""


def run_guide_turn(
    question: str,
    *,
    index: list[IndexedChunk],
    embedder,  # type: ignore[no-untyped-def]
    llm,  # type: ignore[no-untyped-def]
    sink_engine: Engine | None = None,
    conversation_id: str | None = None,
    request_id: str = "guide-turn",
) -> TurnResult:
    """Run one Guide turn and write its redacted event row (best-effort) to the Guide's sink."""
    conv = conversation_id or str(uuid.uuid4())
    guard = guard_inbound(question)  # redact + content guardrail
    redacted_user = guard.redacted_text

    def _emit(
        *,
        decision: str,
        status: str,
        redacted_assistant: str,
        route: str,
        citations: list[Any],
    ) -> None:
        # Best-effort: a sink failure must never break the turn (the audit row is additive to the
        # returned answer/refusal). Redacted-only — there is no raw column to write.
        if sink_engine is None:
            return
        try:
            with Session(sink_engine) as session:
                write_message_event(
                    session,
                    conversation_id=conv,
                    request_id=request_id,
                    guardrail_decision=decision,
                    status=status,
                    redacted_user_msg=redacted_user,
                    redacted_assistant_msg=redacted_assistant,
                    route_or_agent=route,
                    retrieved_chunks=citations,
                )
                session.commit()
        except Exception:  # noqa: BLE001 — best-effort audit; never break the turn
            pass

    # 1. Guardrail block (clinical/injection/secret/participant/redaction) → refuse BEFORE
    #    retrieval/LLM. Participant-data asks get the fixed, terse restricted-information message.
    if guard.result.decision == "blocked":
        route = f"guardrail:{guard.result.category}"
        message = (
            _PARTICIPANT_MSG
            if guard.result.category == "participant"
            else _BLOCKED_MSG
        )
        _emit(
            decision="blocked",
            status="refused",
            redacted_assistant=redact(message),
            route=route,
            citations=[],
        )
        return TurnResult(message, [], "blocked", "refused", route)

    # 2. Allowed → retrieve + grounded/cited answer or sound (relevance) refusal.
    ans = answer_question(redacted_user, index=index, embedder=embedder, llm=llm)
    route = "guide:rag" if ans.status == "ok" else "guide:refuse"
    _emit(
        decision="allowed",
        status=ans.status,
        redacted_assistant=redact(ans.content),
        route=route,
        citations=ans.citations,
    )
    return TurnResult(ans.content, ans.citations, "allowed", ans.status, route)
