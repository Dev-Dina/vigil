"""Gate 5.5b — Anthropic-primary + OpenRouter-fallback failover (pure-unit, NO network).

Covers: failover only on transient errors (429/5xx/timeout/transport), re-raise on auth/400 (no
fallback), provider assembly in get_llm_client (stub-first, primary-missing fail-loud, single vs
failover), and the Anthropic system/message mapping. No live call to either provider.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest

from vigil.agents import llm
from vigil.agents.llm import (
    AnthropicClient,
    FailoverClient,
    LLMError,
    LLMMessage,
    LLMResponse,
    OpenRouterClient,
    StubLLMClient,
    _http_retryable,
    get_llm_client,
)


class _FakeClient:
    """Records calls; either raises a preset LLMError or returns a preset response."""

    def __init__(
        self, *, error: LLMError | None = None, content: str = "ok", model: str = "fake"
    ):
        self._error = error
        self._content = content
        self._model = model
        self.calls = 0

    def complete(self, messages, *, model=None, max_tokens=None, temperature=0.0):  # type: ignore[no-untyped-def]
        self.calls += 1
        if self._error is not None:
            raise self._error
        return LLMResponse(content=self._content, model=self._model)


# --------------------------------------------------------------------------- retryability
def test_http_retryable_classification() -> None:
    assert _http_retryable(429) is True
    assert _http_retryable(500) is True and _http_retryable(503) is True
    assert _http_retryable(401) is False and _http_retryable(400) is False
    assert _http_retryable(None) is True  # transport/timeout, no status → transient


# --------------------------------------------------------------------------- failover behaviour
def test_failover_on_transient_uses_fallback() -> None:
    primary = _FakeClient(
        error=LLMError("rate limited", status_code=429, retryable=True)
    )
    fallback = _FakeClient(content="from-openrouter", model="openrouter/x")
    resp = FailoverClient([primary, fallback]).complete([LLMMessage("user", "hi")])
    assert resp.content == "from-openrouter"
    assert resp.model == "openrouter/x"  # observability shows who actually answered
    assert primary.calls == 1 and fallback.calls == 1


def test_no_failover_on_auth_error_reraises() -> None:
    primary = _FakeClient(
        error=LLMError("unauthorized", status_code=401, retryable=False)
    )
    fallback = _FakeClient(content="should-not-be-used")
    with pytest.raises(LLMError) as ei:
        FailoverClient([primary, fallback]).complete([LLMMessage("user", "hi")])
    assert ei.value.status_code == 401
    assert primary.calls == 1
    assert fallback.calls == 0, "auth error must NOT fall back (misconfig surfaces)"


def test_failover_all_transient_raises_last() -> None:
    p1 = _FakeClient(error=LLMError("a", status_code=503, retryable=True))
    p2 = _FakeClient(error=LLMError("b", status_code=429, retryable=True))
    with pytest.raises(LLMError) as ei:
        FailoverClient([p1, p2]).complete([LLMMessage("user", "hi")])
    assert ei.value.status_code == 429  # last error
    assert p1.calls == 1 and p2.calls == 1


# --------------------------------------------------------------------------- Anthropic mapping
def test_anthropic_splits_system_from_messages() -> None:
    msgs = [
        LLMMessage("system", "You are the router."),
        LLMMessage("user", "why flagged?"),
        LLMMessage("assistant", "prior"),
    ]
    system, turns = AnthropicClient._split_system(msgs)
    assert system == "You are the router."  # system pulled OUT to the top-level param
    assert turns == [
        {"role": "user", "content": "why flagged?"},
        {"role": "assistant", "content": "prior"},
    ]
    assert all(t["role"] != "system" for t in turns)


def test_anthropic_empty_key_fails_loud() -> None:
    with pytest.raises(LLMError):
        AnthropicClient(
            api_key="  \n",
            base_url="https://api.anthropic.com",
            model="claude-haiku-4-5",
            timeout_seconds=1,
            max_tokens=8,
            cost_per_1k_tokens=0.0,
        )


# --------------------------------------------------------------------------- factory assembly
def _settings(**over) -> SimpleNamespace:  # type: ignore[no-untyped-def]
    base = dict(
        llm_stub=False,
        anthropic_enabled=True,
        anthropic_api_key="ak",
        anthropic_base_url="https://api.anthropic.com",
        anthropic_model="claude-haiku-4-5",
        anthropic_cost_per_1k_tokens=0.0,
        llm_api_key="ok",
        llm_base_url="https://openrouter.ai/api/v1",
        llm_model="meta-llama/llama-3.3-70b-instruct:free",
        llm_cost_per_1k_tokens=0.0,
        llm_timeout_seconds=30.0,
        llm_max_tokens=1024,
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_factory_stub_first_covers_both_providers(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # stub selected BEFORE any real client → no key needed for EITHER provider.
    monkeypatch.setattr(
        llm,
        "get_settings",
        lambda: _settings(llm_stub=True, anthropic_api_key="", llm_api_key=""),
    )
    assert isinstance(get_llm_client(), StubLLMClient)


def test_factory_builds_failover_anthropic_primary(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(llm, "get_settings", lambda: _settings())
    client = get_llm_client()
    assert isinstance(client, FailoverClient)
    assert isinstance(client._providers[0], AnthropicClient)  # PRIMARY first
    assert isinstance(client._providers[1], OpenRouterClient)  # fallback


def test_factory_openrouter_only_when_anthropic_disabled(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(llm, "get_settings", lambda: _settings(anthropic_enabled=False))
    assert isinstance(get_llm_client(), OpenRouterClient)


def test_factory_fail_loud_when_primary_key_missing(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    # Anthropic is the configured primary but its key is empty → fail loud (no silent downgrade).
    monkeypatch.setattr(llm, "get_settings", lambda: _settings(anthropic_api_key=""))
    with pytest.raises(LLMError):
        get_llm_client()
