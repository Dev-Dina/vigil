"""The Guide's OWN minimal LLM client (specs/isolation.md § owned code; own key).

Two real clients behind one Protocol, selected by `VIGIL_GUIDE_LLM_PROVIDER` (non-secret config):
an OpenAI-compatible client (`/chat/completions`, Bearer — OpenRouter/OpenAI) and a native Anthropic
client (`/v1/messages`, `x-api-key`). Both read the Guide's OWN key (Vault path
`secret/vigil/guide/llm_api_key`; env shim `VIGIL_GUIDE_LLM_API_KEY`) — distinct from the app's
`vigil/llm/*` keys. Imports nothing from `vigil.*` (no provider SDK — raw httpx only). Hermetic CI:
``VIGIL_GUIDE_LLM_STUB=true`` selects the deterministic stub BEFORE any real client is built, so CI
makes ZERO live calls and needs NO key. ``httpx`` is lazy-imported inside each real client so the
light path stays transport-free.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from guide.config import get_config

# Anthropic Messages API version header (native client). A pinned constant, NOT config — it is a
# protocol version, not a tunable; keeping it off GuideConfig keeps the config-audit surface minimal.
_ANTHROPIC_VERSION = "2023-06-01"


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
    # Gate GUIDE-COST: REAL per-call token usage parsed from the provider response (0 when the
    # provider/stub reports none). The Guide's OWN cost is derived from these (never vigil's code).
    prompt_tokens: int = 0
    completion_tokens: int = 0


def compute_cost(
    prompt_tokens: int,
    completion_tokens: int,
    input_rate_per_1k: float,
    output_rate_per_1k: float,
) -> float:
    """The Guide's OWN split-rate cost helper (NOT vigil's): input×in_rate + output×out_rate per 1k."""
    return round(
        prompt_tokens / 1000 * input_rate_per_1k
        + completion_tokens / 1000 * output_rate_per_1k,
        6,
    )


def provider_cost_rates(cfg) -> tuple[float, float]:  # type: ignore[no-untyped-def]
    """The (input, output) per-1k rates for the configured provider — non-secret config."""
    if cfg.llm_provider == "anthropic":
        return (
            cfg.anthropic_input_cost_per_1k_tokens,
            cfg.anthropic_output_cost_per_1k_tokens,
        )
    return cfg.llm_input_cost_per_1k_tokens, cfg.llm_output_cost_per_1k_tokens


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
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            usage = data.get("usage") or {}
            prompt_tokens = int(usage.get("prompt_tokens", 0) or 0)
            completion_tokens = int(usage.get("completion_tokens", 0) or 0)
        except Exception as exc:  # noqa: BLE001 — fail loud, never a silent empty answer
            raise GuideLLMError(f"Guide LLM call failed: {exc}") from exc
        return GuideLLMResponse(
            content=content,
            model=self._model,
            latency_ms=int((time.monotonic() - started) * 1000),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )


class AnthropicGuideClient:
    """Real client — native Anthropic Messages API with the Guide's OWN key.

    Targets ONLY the configured Anthropic base URL (`POST {base_url}/v1/messages`) with `x-api-key` +
    `anthropic-version` headers. The system message is lifted to the top-level `system` param;
    remaining (user) messages go in `messages`. Imports nothing from `vigil.*` and uses ONLY raw
    httpx — the same transport posture as the OpenAI-compatible client (no `anthropic` SDK).
    """

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

    @staticmethod
    def _split_system(
        messages: list[GuideLLMMessage],
    ) -> tuple[str, list[dict[str, str]]]:
        """Lift the system text out (Anthropic top-level `system`); keep the rest as turns."""
        system = "\n\n".join(m.content for m in messages if m.role == "system")
        turns = [
            {"role": m.role, "content": m.content}
            for m in messages
            if m.role != "system"
        ]
        return system, turns

    def complete(self, messages: list[GuideLLMMessage]) -> GuideLLMResponse:
        import httpx  # deferred — keep the stub/CI path transport-free

        system, turns = self._split_system(messages)
        payload: dict = {
            "model": self._model,
            "max_tokens": self._max_tokens,  # REQUIRED by the Anthropic Messages API
            "temperature": 0.0,
            "messages": turns,
        }
        if system:
            payload["system"] = system
        started = time.monotonic()
        try:
            resp = httpx.post(
                f"{self._base_url}/v1/messages",
                json=payload,
                headers={
                    "x-api-key": self._api_key,
                    "anthropic-version": _ANTHROPIC_VERSION,
                    "content-type": "application/json",
                },
                timeout=self._timeout,
            )
            resp.raise_for_status()
            data = resp.json()
            content = data["content"][0]["text"]
            usage = data.get("usage") or {}
            prompt_tokens = int(usage.get("input_tokens", 0) or 0)
            completion_tokens = int(usage.get("output_tokens", 0) or 0)
        except Exception as exc:  # noqa: BLE001 — fail loud, never a silent empty answer
            raise GuideLLMError(f"Guide LLM call failed: {exc}") from exc
        return GuideLLMResponse(
            content=content,
            model=self._model,
            latency_ms=int((time.monotonic() - started) * 1000),
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )


def get_guide_llm_client() -> GuideLLMClient:
    """Stub FIRST (CI-hermetic), else the real client for the configured provider (own key).

    Selection: stub → (provider == "anthropic" ? native Anthropic : OpenAI-compatible). The provider
    is non-secret config (`VIGIL_GUIDE_LLM_PROVIDER`); the key stays the only secret.
    """
    cfg = get_config()
    if cfg.llm_stub:
        return StubGuideLLMClient()
    if cfg.llm_provider == "anthropic":
        return AnthropicGuideClient(
            api_key=cfg.llm_api_key,
            base_url=cfg.llm_base_url,
            model=cfg.llm_model,
            timeout_seconds=cfg.llm_timeout_seconds,
            max_tokens=cfg.llm_max_tokens,
        )
    return OpenRouterGuideClient(
        api_key=cfg.llm_api_key,
        base_url=cfg.llm_base_url,
        model=cfg.llm_model,
        timeout_seconds=cfg.llm_timeout_seconds,
        max_tokens=cfg.llm_max_tokens,
    )
