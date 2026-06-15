"""LLM client — OpenRouter generation (specs/rag.md § Decisions: generation via OpenRouter).

The agent layer is the project's FIRST outbound LLM egress. Egress is **allow-listed to the
OpenRouter base URL only** (deny-by-default). CI is hermetic: ``VIGIL_LLM_STUB=true`` selects the
deterministic :class:`StubLLMClient` — no network, no key — so the router/agent/eval tests are
reproducible. Real OpenRouter is reached only on local/demo runs.

Fail-loud: a transport/HTTP/parse failure raises :class:`LLMError` (never a silent empty answer).
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from vigil.core.config import get_settings


class LLMError(RuntimeError):
    """Raised on any LLM transport / HTTP / parse failure — never swallowed."""


@dataclass(frozen=True, slots=True)
class LLMMessage:
    role: str  # "system" | "user" | "assistant"
    content: str


@dataclass(frozen=True, slots=True)
class LLMResponse:
    content: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0
    cost_estimate: float = 0.0
    latency_ms: int = 0


@runtime_checkable
class LLMClient(Protocol):
    """The only contract the agent layer depends on for generation."""

    def complete(
        self,
        messages: list[LLMMessage],
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse: ...


def _cost(prompt_tokens: int, completion_tokens: int, rate_per_1k: float) -> float:
    return round((prompt_tokens + completion_tokens) / 1000.0 * rate_per_1k, 6)


class OpenRouterClient:
    """Real client. Targets ONLY the configured OpenRouter base URL (egress allow-list).

    ``httpx`` is imported lazily inside :meth:`complete` so importing this module never pulls a
    transport in (keeps the stub path — and CI — free of any network library at import time).
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float,
        max_tokens: int,
        cost_per_1k_tokens: float,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._api_key = api_key
        self._model = model
        self._timeout = timeout_seconds
        self._max_tokens = max_tokens
        self._rate = cost_per_1k_tokens

    def complete(
        self,
        messages: list[LLMMessage],
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse:
        import httpx  # deferred — keep the stub/CI path import-free of a transport

        mdl = model or self._model
        payload = {
            "model": mdl,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "max_tokens": max_tokens or self._max_tokens,
            "temperature": temperature,
        }
        url = f"{self.base_url}/chat/completions"
        started = time.monotonic()
        try:
            resp = httpx.post(
                url,
                json=payload,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage", {})
            prompt_tokens = int(usage.get("prompt_tokens", 0))
            completion_tokens = int(usage.get("completion_tokens", 0))
        except Exception as exc:  # noqa: BLE001 — fail loud, never a silent empty answer
            raise LLMError(f"OpenRouter completion failed: {exc}") from exc
        latency_ms = int((time.monotonic() - started) * 1000)
        return LLMResponse(
            content=content,
            model=mdl,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_estimate=_cost(prompt_tokens, completion_tokens, self._rate),
            latency_ms=latency_ms,
        )


class StubLLMClient:
    """Deterministic, hermetic client for CI/tests — no network, no key.

    Returns a recorded response keyed by the last user message's exact text, else ``default``.
    Records every call in :attr:`calls` so tests can assert what was (and was not) sent.
    """

    def __init__(
        self,
        responses: dict[str, str] | None = None,
        *,
        default: str = "[STUB] grounded answer",
        model: str = "stub/deterministic",
    ) -> None:
        self._responses = responses or {}
        self._default = default
        self._model = model
        self.calls: list[list[LLMMessage]] = []

    def complete(
        self,
        messages: list[LLMMessage],
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse:
        self.calls.append(list(messages))
        last_user = next(
            (m.content for m in reversed(messages) if m.role == "user"), ""
        )
        content = self._responses.get(last_user, self._default)
        # Deterministic pseudo-usage so cost/latency fields are populated but stable.
        prompt_tokens = sum(len(m.content.split()) for m in messages)
        completion_tokens = len(content.split())
        return LLMResponse(
            content=content,
            model=model or self._model,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_estimate=0.0,
            latency_ms=0,
        )


def get_llm_client() -> LLMClient:
    """Return the active LLM client: the hermetic stub when ``llm_stub`` is set, else OpenRouter.

    Explicit, not a silent fallback — CI sets ``VIGIL_LLM_STUB=true``; local/demo uses the real
    OpenRouter client (key sourced from Vault/env via settings, never inlined).
    """
    settings = get_settings()
    if settings.llm_stub:
        return StubLLMClient()
    return OpenRouterClient(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        model=settings.llm_model,
        timeout_seconds=settings.llm_timeout_seconds,
        max_tokens=settings.llm_max_tokens,
        cost_per_1k_tokens=settings.llm_cost_per_1k_tokens,
    )
