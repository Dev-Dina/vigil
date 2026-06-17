"""The Guide's OWN minimal LLM client (specs/isolation.md § owned code; own key).

A small OpenRouter-style client that reads the Guide's OWN key (Vault path
`secret/vigil/guide/llm_api_key`; env shim `VIGIL_GUIDE_LLM_API_KEY`) — distinct from the app's
`vigil/llm/*` keys. Imports nothing from `vigil.*`. Hermetic CI: ``VIGIL_GUIDE_LLM_STUB=true``
selects the deterministic stub BEFORE any real client is built, so CI makes ZERO live calls and
needs NO key. ``httpx`` is lazy-imported inside the real client so the light path stays
transport-free.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from guide.config import get_config


class GuideLLMError(RuntimeError):
    """Raised on any Guide LLM transport/HTTP/parse failure — never a silent empty answer."""


@dataclass(frozen=True, slots=True)
class GuideLLMMessage:
    role: str  # "system" | "user"
    content: str


@dataclass(frozen=True, slots=True)
class GuideLLMResponse:
    content: str
    model: str
    latency_ms: int = 0


@runtime_checkable
class GuideLLMClient(Protocol):
    def complete(self, messages: list[GuideLLMMessage]) -> GuideLLMResponse: ...


class StubGuideLLMClient:
    """Deterministic, hermetic client for CI/tests — no network, no key."""

    def __init__(
        self, *, default: str = "[STUB] grounded answer from approved docs"
    ) -> None:
        self._default = default
        self.calls: list[list[GuideLLMMessage]] = []

    def complete(self, messages: list[GuideLLMMessage]) -> GuideLLMResponse:
        self.calls.append(list(messages))
        return GuideLLMResponse(content=self._default, model="stub/guide")


class OpenRouterGuideClient:
    """Real client — targets ONLY the configured OpenRouter base URL with the Guide's own key."""

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float,
        max_tokens: int,
    ) -> None:
        self._api_key = (api_key or "").strip()
        if not self._api_key:
            raise GuideLLMError(
                "Guide LLM key is empty — set secret/vigil/guide/llm_api_key (Vault) "
                "or VIGIL_GUIDE_LLM_API_KEY (dev shim)."
            )
        self._base_url = base_url.rstrip("/")
        self._model = model
        self._timeout = timeout_seconds
        self._max_tokens = max_tokens

    def complete(self, messages: list[GuideLLMMessage]) -> GuideLLMResponse:
        import httpx  # deferred — keep the stub/CI path transport-free

        payload = {
            "model": self._model,
            "messages": [{"role": m.role, "content": m.content} for m in messages],
            "max_tokens": self._max_tokens,
            "temperature": 0.0,
        }
        started = time.monotonic()
        try:
            resp = httpx.post(
                f"{self._base_url}/chat/completions",
                json=payload,
                headers={"Authorization": f"Bearer {self._api_key}"},
                timeout=self._timeout,
            )
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
        except Exception as exc:  # noqa: BLE001 — fail loud, never a silent empty answer
            raise GuideLLMError(f"Guide LLM call failed: {exc}") from exc
        return GuideLLMResponse(
            content=content,
            model=self._model,
            latency_ms=int((time.monotonic() - started) * 1000),
        )


def get_guide_llm_client() -> GuideLLMClient:
    """Stub FIRST (CI-hermetic), else the real OpenRouter client with the Guide's own key."""
    cfg = get_config()
    if cfg.llm_stub:
        return StubGuideLLMClient()
    return OpenRouterGuideClient(
        api_key=cfg.llm_api_key,
        base_url=cfg.llm_base_url,
        model=cfg.llm_model,
        timeout_seconds=cfg.llm_timeout_seconds,
        max_tokens=cfg.llm_max_tokens,
    )
