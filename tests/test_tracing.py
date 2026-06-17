"""Gate 6.3 — Langfuse tracing (pure-unit, hermetic; NO network, NO key, NO SDK build).

Covers: redacted-only by construction (TurnTrace has no raw field), CI-hermetic tracer selection
(disabled → NullTracer; enabled but build fails → best-effort fallback to NullTracer), safe_trace
swallows a failing tracer, and the SDK is lazy-imported (importing the module pulls no langfuse).
"""

from __future__ import annotations

import subprocess
import sys
from types import SimpleNamespace

from vigil.agents import tracing
from vigil.agents.tracing import NullTracer, TurnTrace, get_tracer, safe_trace


def _trace(**over) -> TurnTrace:  # type: ignore[no-untyped-def]
    base = dict(
        conversation_id="c",
        request_id="r",
        surface="local_assistant",
        role_or_guest_scope="coordinator",
        route_or_agent="agent:retention",
        guardrail_decision="allowed",
        status="ok",
        llm_provider_model="anthropic/claude-haiku-4-5",
        latency_ms=10,
        token_cost_estimate=0.0,
        redacted_user_msg="[REDACTED]",
        redacted_assistant_msg="[REDACTED]",
        retrieved_chunks=[],
    )
    base.update(over)
    return TurnTrace(**base)


# --------------------------------------------------------------------------- redacted-only
def test_turntrace_has_no_raw_field() -> None:
    fields = set(TurnTrace.__dataclass_fields__)
    # Only redacted text carries message content; there is NO raw-content field by construction.
    assert {"redacted_user_msg", "redacted_assistant_msg"} <= fields
    assert not any("raw" in f for f in fields)
    for forbidden in ("user_msg", "assistant_msg", "content", "message"):
        assert forbidden not in fields, f"raw-ish field {forbidden!r} on TurnTrace"


# --------------------------------------------------------------------------- tracer selection
def test_get_tracer_disabled_is_null() -> None:
    # No monkeypatch needed: the test env has VIGIL_LANGFUSE_ENABLED unset/false → NullTracer.
    assert isinstance(get_tracer(), NullTracer)


def test_get_tracer_enabled_build_failure_falls_back(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        tracing,
        "get_settings",
        lambda: SimpleNamespace(
            langfuse_enabled=True,
            langfuse_public_key="pk",
            langfuse_secret_key="sk",
            langfuse_host="https://langfuse.example",
        ),
    )

    def _boom(**_kw):  # type: ignore[no-untyped-def]
        raise RuntimeError("sdk init failed")

    monkeypatch.setattr(tracing, "LangfuseTracer", _boom)
    # Best-effort: a tracer-build failure must fall back to NullTracer, never break the caller.
    assert isinstance(get_tracer(), NullTracer)


# --------------------------------------------------------------------------- best-effort
def test_safe_trace_swallows_failure() -> None:
    class _Boom:
        def trace_turn(self, _t: TurnTrace) -> None:
            raise RuntimeError("langfuse down")

    safe_trace(_Boom(), _trace())  # must NOT raise


def test_null_tracer_is_noop() -> None:
    assert NullTracer().trace_turn(_trace()) is None


# --------------------------------------------------------------------------- lazy import
def test_module_import_does_not_load_langfuse_sdk() -> None:
    # Importing the tracing module (light path) must NOT pull the langfuse SDK — it is lazy-imported
    # only inside LangfuseTracer, like httpx/anthropic/torch. Subprocess = clean module table.
    code = (
        "import sys; import vigil.agents.tracing as t; "
        "assert 'langfuse' not in sys.modules, 'langfuse imported eagerly'; print('ok')"
    )
    proc = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    assert "ok" in proc.stdout
