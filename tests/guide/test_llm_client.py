"""Gate GUIDE-ANTHROPIC — the Guide's native Anthropic client + provider selection (hermetic).

No live call: httpx.post is monkeypatched. Asserts the native Anthropic request shape
(`/v1/messages`, `x-api-key` + `anthropic-version`, system split out, `max_tokens` + `temperature`),
the `content[0].text` parse, the empty-key fail-loud, and that `get_guide_llm_client()` selects
stub-first → then the provider's client. Imports nothing from `vigil.*` (Guide-owned).
"""

from __future__ import annotations

from types import SimpleNamespace

import httpx
import pytest

from guide.llm import (
    _ANTHROPIC_VERSION,
    AnthropicGuideClient,
    GuideLLMError,
    GuideLLMMessage,
    GuideLLMResponse,
    OpenRouterGuideClient,
    StubGuideLLMClient,
    get_guide_llm_client,
)


class _FakeResponse:
    def __init__(self, payload: dict, *, status_ok: bool = True) -> None:
        self._payload = payload
        self._ok = status_ok

    def raise_for_status(self) -> None:
        if not self._ok:
            raise RuntimeError("HTTP 401")

    def json(self) -> dict:
        return self._payload


def _client() -> AnthropicGuideClient:
    return AnthropicGuideClient(
        api_key="sk-ant-test",
        base_url="https://api.anthropic.com",
        model="claude-haiku-4-5",
        timeout_seconds=5.0,
        max_tokens=256,
    )


# --------------------------------------------------------------------------- native request shape
def test_anthropic_client_posts_native_messages_shape(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    captured: dict = {}

    def _fake_post(url, *, json, headers, timeout):  # type: ignore[no-untyped-def]
        captured.update(url=url, json=json, headers=headers, timeout=timeout)
        return _FakeResponse({"content": [{"text": "grounded claude answer"}]})

    monkeypatch.setattr(httpx, "post", _fake_post)

    resp = _client().complete(
        [
            GuideLLMMessage("system", "SYSTEM PROMPT"),
            GuideLLMMessage("user", "Approved context:\n...\n\nQuestion: What is Vigil?"),
        ]
    )

    # Endpoint = native Anthropic Messages API.
    assert captured["url"] == "https://api.anthropic.com/v1/messages"
    # Native headers — x-api-key (NOT Bearer) + pinned anthropic-version.
    assert captured["headers"]["x-api-key"] == "sk-ant-test"
    assert captured["headers"]["anthropic-version"] == _ANTHROPIC_VERSION == "2023-06-01"
    assert captured["headers"]["content-type"] == "application/json"
    assert "authorization" not in {k.lower() for k in captured["headers"]}
    # Body: system lifted to the top-level param; only the user turn in `messages`.
    body = captured["json"]
    assert body["model"] == "claude-haiku-4-5"
    assert body["max_tokens"] == 256  # REQUIRED by Anthropic
    assert body["temperature"] == 0.0
    assert body["system"] == "SYSTEM PROMPT"
    assert body["messages"] == [
        {"role": "user", "content": "Approved context:\n...\n\nQuestion: What is Vigil?"}
    ]
    assert all(m["role"] != "system" for m in body["messages"])
    # Response parsed from content[0].text into the SAME GuideLLMResponse interface.
    assert isinstance(resp, GuideLLMResponse)
    assert resp.content == "grounded claude answer"
    assert resp.model == "claude-haiku-4-5"
    assert resp.latency_ms >= 0


def test_anthropic_client_empty_key_fails_loud() -> None:
    with pytest.raises(GuideLLMError):
        AnthropicGuideClient(
            api_key="  \n",
            base_url="https://api.anthropic.com",
            model="claude-haiku-4-5",
            timeout_seconds=5.0,
            max_tokens=256,
        )


def test_anthropic_client_http_error_fails_loud(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    def _bad_post(url, *, json, headers, timeout):  # type: ignore[no-untyped-def]
        return _FakeResponse({}, status_ok=False)

    monkeypatch.setattr(httpx, "post", _bad_post)
    with pytest.raises(GuideLLMError):
        _client().complete([GuideLLMMessage("user", "hi")])


# --------------------------------------------------------------------------- factory selection
def _cfg(**over) -> SimpleNamespace:  # type: ignore[no-untyped-def]
    base = dict(
        llm_stub=False,
        llm_provider="openai_compatible",
        llm_api_key="key",
        llm_base_url="https://openrouter.ai/api/v1",
        llm_model="meta-llama/llama-3.3-70b-instruct:free",
        llm_timeout_seconds=30.0,
        llm_max_tokens=1024,
    )
    base.update(over)
    return SimpleNamespace(**base)


def test_factory_stub_first_regardless_of_provider(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import guide.llm as llm_mod

    monkeypatch.setattr(
        llm_mod, "get_config", lambda: _cfg(llm_stub=True, llm_provider="anthropic")
    )
    assert isinstance(get_guide_llm_client(), StubGuideLLMClient)


def test_factory_builds_anthropic_when_provider_anthropic(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import guide.llm as llm_mod

    monkeypatch.setattr(
        llm_mod,
        "get_config",
        lambda: _cfg(
            llm_provider="anthropic",
            llm_base_url="https://api.anthropic.com",
            llm_model="claude-haiku-4-5",
        ),
    )
    assert isinstance(get_guide_llm_client(), AnthropicGuideClient)


def test_factory_default_provider_is_openai_compatible(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import guide.llm as llm_mod

    monkeypatch.setattr(llm_mod, "get_config", lambda: _cfg())
    assert isinstance(get_guide_llm_client(), OpenRouterGuideClient)
