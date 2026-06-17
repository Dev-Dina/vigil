"""Langfuse per-turn tracing (specs/observability.md § Phase 6 contracts § Langfuse).

The richer per-turn TRACE on top of the durable ``message_events`` audit record. Langfuse holds
the full trace (router decision, agent, retrieval, LLM usage, guardrail outcome, latency);
``message_events`` stays the durable, queryable record written every turn regardless.

Three invariants enforced here:
- **REDACTED-ONLY (safety-critical).** :class:`TurnTrace` has NO raw-content field — only the
  ``redacted_*`` text that already went to ``message_events``. Raw user content cannot reach
  Langfuse by construction (same posture as the ``message_events`` write path).
- **CI-HERMETIC.** ``get_tracer`` returns a no-op :class:`NullTracer` unless ``langfuse_enabled``
  (default FALSE) — selected BEFORE any key read or SDK import, so CI/tests make ZERO Langfuse
  calls and need NO key (mirrors ``VIGIL_LLM_STUB``). The ``langfuse`` SDK is lazy-imported inside
  the real tracer, so importing this module never pulls it in (like ``httpx``/``anthropic``).
- **ADDITIVE / BEST-EFFORT.** Every tracer call is wrapped: a disabled or failing Langfuse NEVER
  raises into the turn — the mandatory ``message_events`` write still lands. Egress: the Langfuse
  host is a NEW outbound destination, allow-listed (deny-by-default, /specs/isolation.md).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol, runtime_checkable

from vigil.core.config import get_settings
from vigil.core.logging import get_logger

log = get_logger("vigil.agents.tracing")


@dataclass(frozen=True, slots=True)
class TurnTrace:
    """A single assistant-turn trace payload — REDACTED-ONLY by construction.

    There is deliberately NO raw user/assistant field: only the redacted text that also goes to
    ``message_events``, plus structural metadata. Raw content has no path into a trace.
    """

    conversation_id: str
    request_id: str
    surface: str
    role_or_guest_scope: str
    route_or_agent: str
    guardrail_decision: str
    status: str
    llm_provider_model: str
    latency_ms: int
    token_cost_estimate: float
    redacted_user_msg: str
    redacted_assistant_msg: str
    retrieved_chunks: list[Any] = field(default_factory=list)


@runtime_checkable
class Tracer(Protocol):
    """The only contract the turn pipeline depends on for tracing."""

    def trace_turn(self, trace: TurnTrace) -> None: ...


class NullTracer:
    """No-op tracer — the CI/hermetic + disabled default. Makes no call, needs no key."""

    def trace_turn(self, trace: TurnTrace) -> None:  # noqa: D401 - intentional no-op
        return None


class LangfuseTracer:
    """Real Langfuse tracer. Lazy-imports the SDK; every call is best-effort (never raises)."""

    def __init__(self, *, public_key: str, secret_key: str, host: str) -> None:
        from langfuse import (
            Langfuse,
        )  # deferred — keep the import light/hermetic path SDK-free

        self._client = Langfuse(public_key=public_key, secret_key=secret_key, host=host)

    def trace_turn(self, trace: TurnTrace) -> None:
        # Send ONLY redacted text + structural metadata. input/output are the redacted messages —
        # the raw text never existed on the TurnTrace, so it cannot be sent. ``create_event`` is
        # the Langfuse v4 one-shot observation for a single turn; metadata carries the structural
        # trace (router/agent, retrieval, model, guardrail, latency, cost).
        self._client.create_event(
            name="assistant_turn",
            input=trace.redacted_user_msg,
            output=trace.redacted_assistant_msg,
            level="WARNING" if trace.guardrail_decision == "blocked" else "DEFAULT",
            metadata={
                "conversation_id": trace.conversation_id,
                "request_id": trace.request_id,
                "surface": trace.surface,
                "role_or_guest_scope": trace.role_or_guest_scope,
                "route_or_agent": trace.route_or_agent,
                "guardrail_decision": trace.guardrail_decision,
                "status": trace.status,
                "llm_provider_model": trace.llm_provider_model,
                "latency_ms": trace.latency_ms,
                "token_cost_estimate": trace.token_cost_estimate,
                "retrieved_chunks": trace.retrieved_chunks,
            },
        )
        self._client.flush()  # short-lived worker: deliver before the job ends (best-effort)


def get_tracer() -> Tracer:
    """Return the active tracer: :class:`NullTracer` unless ``langfuse_enabled`` (CI-hermetic).

    The disabled branch is checked FIRST — no key is read and the ``langfuse`` SDK is never
    imported, so CI/tests make zero Langfuse calls and need no Vault key. Building the real tracer
    is itself best-effort: if a key read or SDK init fails, fall back to the NullTracer rather than
    breaking the turn (tracing is additive; the durable ``message_events`` write is mandatory).
    """
    settings = get_settings()
    if not settings.langfuse_enabled:
        return NullTracer()
    try:
        return LangfuseTracer(
            public_key=settings.langfuse_public_key,
            secret_key=settings.langfuse_secret_key,
            host=settings.langfuse_host,
        )
    except Exception:  # noqa: BLE001 - tracing is best-effort; never break the turn on setup
        log.warning("langfuse.tracer_init_failed", extra={"extra": {"fell_back": True}})
        return NullTracer()


def safe_trace(tracer: Tracer, trace: TurnTrace) -> None:
    """Emit a trace best-effort: a Langfuse failure is logged and swallowed, NEVER raised.

    The turn and its mandatory ``message_events`` write are already done by the time this runs;
    tracing is the additive layer and must not break either.
    """
    try:
        tracer.trace_turn(trace)
    except Exception:  # noqa: BLE001 - additive/best-effort: trace failure must not break the turn
        log.warning(
            "langfuse.trace_failed",
            extra={"extra": {"request_id": trace.request_id, "swallowed": True}},
        )
