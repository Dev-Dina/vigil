"""Gate 6.3 — Langfuse tracing through a real assistant turn (spine, real Postgres, hermetic).

Proves the 6.0 Langfuse contract end-to-end:
- CI-HERMETIC: with tracing disabled (the test/CI env) the tracer is a NullTracer — a turn runs
  fully and writes its durable message_events row, making NO Langfuse call and needing no key.
- REDACTED-ONLY: against a captured fake tracer, the emitted TurnTrace carries only redacted text
  (raw PII in the inbound message never reaches the trace).
- ADDITIVE/BEST-EFFORT: a failing tracer does NOT break the turn — the message_events row lands.

Langfuse stays OFF (conftest sets VIGIL_LANGFUSE_ENABLED=false); fakes are injected via the
``_tracer_override`` ctx hook, so no live Langfuse call is ever made.
"""

from __future__ import annotations

import asyncio
import dataclasses
import uuid

from sqlalchemy import select

from vigil.agents.llm import LLMResponse
from vigil.agents.tracing import NullTracer, TurnTrace, get_tracer
from vigil.db.models import MessageEvent
from vigil.repositories.session import sponsor_bootstrap_session
from vigil.workers.tasks import run_assistant_turn

_PII_EMAIL = "john.doe@example.com"


class ScriptedLLM:
    """Hermetic stub: routes by SYSTEM prompt (router vs agent) → canned output."""

    def __init__(
        self, *, route_to: str = "retention", answer: str = "grounded"
    ) -> None:
        self._route_to = route_to
        self._answer = answer

    def complete(self, messages, *, model=None, max_tokens=None, temperature=0.0):  # type: ignore[no-untyped-def]
        system = next((m.content for m in messages if m.role == "system"), "")
        content = (
            f'{{"agent": "{self._route_to}", "reason": "stub"}}'
            if "ROUTER" in system
            else self._answer
        )
        return LLMResponse(content=content, model="scripted/stub")


class CapturingTracer:
    """Records every TurnTrace it receives — to assert redacted-only payloads."""

    def __init__(self) -> None:
        self.traces: list[TurnTrace] = []

    def trace_turn(self, trace: TurnTrace) -> None:
        self.traces.append(trace)


class FailingTracer:
    """Always raises — to prove tracing is best-effort and never breaks the turn."""

    def __init__(self) -> None:
        self.called = False

    def trace_turn(self, trace: TurnTrace) -> None:
        self.called = True
        raise RuntimeError("langfuse unreachable")


def _run(ids, *, user_id, content, llm, tracer=None) -> dict:  # type: ignore[no-untyped-def]
    conv = str(uuid.uuid4())
    ctx: dict = {"job_try": 0, "_llm_override": llm}
    if tracer is not None:
        ctx["_tracer_override"] = tracer
    res = asyncio.get_event_loop().run_until_complete(
        run_assistant_turn(
            ctx,
            conversation_id=conv,
            user_id=user_id,
            content=content,
            sponsor_id=ids["sponsor_a"],
            request_id="trace-req",
        )
    )
    res["_conversation_id"] = conv
    return res


def _events(ids, conv: str) -> list[MessageEvent]:
    with sponsor_bootstrap_session(ids["sponsor_a"]) as session:
        return list(
            session.execute(
                select(MessageEvent).where(
                    MessageEvent.conversation_id == uuid.UUID(conv)
                )
            ).scalars()
        )


# ---------------------------------------------------------------------------
# 1. CI-HERMETIC: tracing disabled → NullTracer; turn writes its row, no Langfuse call
# ---------------------------------------------------------------------------


def test_disabled_tracer_is_null_and_turn_still_writes(
    migrated_db: dict[str, str],
) -> None:
    # The test/CI env has Langfuse OFF → get_tracer() is the no-op NullTracer (no SDK, no egress).
    assert isinstance(get_tracer(), NullTracer)

    res = _run(
        ids=migrated_db,
        user_id=migrated_db["coordinator_a"],
        content="How many high-risk participants are in my trial?",
        llm=ScriptedLLM(route_to="report", answer="See the cohort summary."),
    )
    # The durable record lands regardless of tracing.
    rows = _events(migrated_db, res["_conversation_id"])
    assert len(rows) == 1


# ---------------------------------------------------------------------------
# 2. REDACTED-ONLY: the trace payload never carries raw inbound content
# ---------------------------------------------------------------------------


def test_trace_payload_is_redacted_only(migrated_db: dict[str, str]) -> None:
    tracer = CapturingTracer()
    res = _run(
        ids=migrated_db,
        user_id=migrated_db["coordinator_a"],
        content=f"Why is this participant flagged? Reach me at {_PII_EMAIL}",
        llm=ScriptedLLM(route_to="retention", answer="grounded"),
        tracer=tracer,
    )
    assert res["_conversation_id"]
    assert tracer.traces, "a turn must emit exactly one trace"
    tr = tracer.traces[-1]
    # The inbound PII was redacted before the trace; the raw email is nowhere in the payload.
    assert _PII_EMAIL not in tr.redacted_user_msg
    assert "[REDACTED_EMAIL]" in tr.redacted_user_msg
    for value in dataclasses.asdict(tr).values():
        assert _PII_EMAIL not in str(value), "raw PII leaked into the trace payload"


# ---------------------------------------------------------------------------
# 3. ADDITIVE / BEST-EFFORT: a failing tracer does not break the turn or the audit row
# ---------------------------------------------------------------------------


def test_tracer_failure_does_not_break_turn_or_audit(
    migrated_db: dict[str, str],
) -> None:
    tracer = FailingTracer()
    res = _run(
        ids=migrated_db,
        user_id=migrated_db["coordinator_a"],
        content="How many high-risk participants are in my trial?",
        llm=ScriptedLLM(route_to="report", answer="ok"),
        tracer=tracer,
    )
    assert tracer.called, "the tracer was invoked (and raised)"
    # Despite the trace failure, the turn completed and the durable audit row was written.
    assert res["guardrail_decision"] in ("allowed", "blocked")
    rows = _events(migrated_db, res["_conversation_id"])
    assert len(rows) == 1, "message_events row must land even when tracing fails"
