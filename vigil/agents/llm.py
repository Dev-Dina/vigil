"""LLM clients — Anthropic PRIMARY + OpenRouter FALLBACK (specs/rag.md § Decisions).

Generation calls Anthropic (`claude-haiku-4-5`) first; on a TRANSIENT error (429 / 5xx / timeout /
transport) the :class:`FailoverClient` retries OpenRouter. On a NON-transient error (401 auth /
400 bad request) it RE-RAISES — a primary misconfig surfaces loudly, never masked. The agent layer
is the project's FIRST outbound LLM egress: egress is **allow-listed to api.anthropic.com +
openrouter.ai only** (deny-by-default). CI is hermetic: ``VIGIL_LLM_STUB=true`` selects the
deterministic :class:`StubLLMClient` — chosen BEFORE any real client is built, so zero live calls
to EITHER provider and no keys needed. Real providers are reached only on local/demo runs.

Fail-loud: a transport/HTTP/parse failure raises :class:`LLMError` (carrying ``status_code`` +
``retryable``), never a silent empty answer. The SDKs (`anthropic`, `httpx`) are lazy-imported
inside each client's ``complete`` so the stub/golden/check_specs import paths stay SDK-free.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

from vigil.core.config import get_settings
from vigil.core.logging import get_logger

log = get_logger("vigil.agents.llm")


class LLMError(RuntimeError):
    """Raised on any LLM transport / HTTP / parse failure — never swallowed.

    Carries ``status_code`` (HTTP status, or None for transport/parse) and ``retryable``:
    True for transient failures (429 / 5xx / timeout / transport) that the FailoverClient may
    retry on the next provider; False for non-transient ones (401 auth / 400 bad request) that
    MUST surface — a primary misconfig is never masked by silent failover.
    """

    def __init__(
        self, message: str, *, status_code: int | None = None, retryable: bool = False
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.retryable = retryable


def _http_retryable(status_code: int | None) -> bool:
    """429 (rate limit) and 5xx (server) are transient → failover; 4xx (e.g. 401/400) are not."""
    if status_code is None:
        return True  # transport/timeout with no HTTP status → transient
    return status_code == 429 or status_code >= 500


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


def _cost(
    prompt_tokens: int,
    completion_tokens: int,
    input_rate_per_1k: float,
    output_rate_per_1k: float,
) -> float:
    """USD cost = ``input_tokens × input_rate + output_tokens × output_rate`` (per 1k tokens).

    Input and output are priced SEPARATELY (Gate COST-1): Anthropic charges output ~5× input, so a
    single blended rate would misstate the real cost. Honest-zero when both rates are 0 (e.g. the
    free OpenRouter fallback model). Real token counts in, real (small) dollar cost out.
    """
    return round(
        prompt_tokens / 1000.0 * input_rate_per_1k
        + completion_tokens / 1000.0 * output_rate_per_1k,
        6,
    )


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
        input_cost_per_1k_tokens: float,
        output_cost_per_1k_tokens: float,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        # Strip stray whitespace/newline a secret store can introduce (e.g. `value="$(cat key)"`
        # or a here-doc trailing newline). A `Bearer <key>\n` header → 401; fail loud if empty.
        self._api_key = (api_key or "").strip()
        if not self._api_key:
            raise LLMError(
                "OpenRouter API key is empty after trim — check secret/vigil/llm/api_key "
                "(field 'value') in Vault"
            )
        self._model = model
        self._timeout = timeout_seconds
        self._max_tokens = max_tokens
        self._input_rate = input_cost_per_1k_tokens
        self._output_rate = output_cost_per_1k_tokens

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
        except httpx.HTTPStatusError as exc:
            sc = exc.response.status_code
            raise LLMError(
                f"OpenRouter HTTP {sc}", status_code=sc, retryable=_http_retryable(sc)
            ) from exc
        except httpx.HTTPError as exc:  # transport/timeout — no HTTP status → transient
            raise LLMError(
                f"OpenRouter transport error: {exc}", status_code=None, retryable=True
            ) from exc
        except Exception as exc:  # noqa: BLE001 — parse/shape error: non-transient, fail loud
            raise LLMError(
                f"OpenRouter response error: {exc}", status_code=None, retryable=False
            ) from exc
        latency_ms = int((time.monotonic() - started) * 1000)
        return LLMResponse(
            content=content,
            model=mdl,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_estimate=_cost(
                prompt_tokens, completion_tokens, self._input_rate, self._output_rate
            ),
            latency_ms=latency_ms,
        )


class AnthropicClient:
    """PRIMARY client — Anthropic Messages API. Egress allow-listed to api.anthropic.com.

    ``anthropic`` is imported lazily inside :meth:`complete` so importing this module never pulls
    the SDK in (keeps the stub / golden / check_specs import paths free of it, like httpx/torch).
    Maps our messages to the Messages API: SYSTEM messages → the top-level ``system=`` param
    (Anthropic takes system SEPARATELY, not as a role); user/assistant → ``messages``. Raises
    :class:`LLMError` with ``status_code`` + ``retryable`` so the FailoverClient can decide.
    """

    def __init__(
        self,
        *,
        api_key: str,
        base_url: str,
        model: str,
        timeout_seconds: float,
        max_tokens: int,
        input_cost_per_1k_tokens: float,
        output_cost_per_1k_tokens: float,
    ) -> None:
        self.base_url = base_url.rstrip("/")
        self._api_key = (api_key or "").strip()
        if not self._api_key:
            raise LLMError(
                "Anthropic API key is empty after trim — check "
                "secret/vigil/llm/anthropic_api_key (field 'value') in Vault"
            )
        self._model = model
        self._timeout = timeout_seconds
        self._max_tokens = max_tokens
        self._input_rate = input_cost_per_1k_tokens
        self._output_rate = output_cost_per_1k_tokens

    @staticmethod
    def _split_system(messages: list[LLMMessage]) -> tuple[str, list[dict]]:
        """System messages → a single ``system`` string; the rest → user/assistant messages."""
        system_parts = [m.content for m in messages if m.role == "system"]
        turns = [
            {"role": m.role, "content": m.content}
            for m in messages
            if m.role in ("user", "assistant")
        ]
        return "\n\n".join(system_parts), turns

    def complete(
        self,
        messages: list[LLMMessage],
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse:
        import anthropic  # deferred — keep the stub/CI/golden import paths SDK-free

        mdl = model or self._model
        system, turns = self._split_system(messages)
        client = anthropic.Anthropic(
            api_key=self._api_key, base_url=self.base_url, timeout=self._timeout
        )
        started = time.monotonic()
        try:
            resp = client.messages.create(
                model=mdl,
                system=system,
                messages=turns,
                max_tokens=max_tokens or self._max_tokens,
                temperature=temperature,
            )
            content = "".join(
                block.text
                for block in resp.content
                if getattr(block, "type", None) == "text"
            )
            prompt_tokens = int(getattr(resp.usage, "input_tokens", 0) or 0)
            completion_tokens = int(getattr(resp.usage, "output_tokens", 0) or 0)
        except anthropic.APIStatusError as exc:
            sc = exc.status_code
            raise LLMError(
                f"Anthropic HTTP {sc}", status_code=sc, retryable=_http_retryable(sc)
            ) from exc
        except anthropic.APITimeoutError as exc:
            raise LLMError(
                f"Anthropic timeout: {exc}", status_code=None, retryable=True
            ) from exc
        except anthropic.APIConnectionError as exc:  # transport — transient
            raise LLMError(
                f"Anthropic transport error: {exc}", status_code=None, retryable=True
            ) from exc
        except Exception as exc:  # noqa: BLE001 — parse/shape: non-transient, fail loud
            raise LLMError(
                f"Anthropic response error: {exc}", status_code=None, retryable=False
            ) from exc
        latency_ms = int((time.monotonic() - started) * 1000)
        return LLMResponse(
            content=content,
            model=f"anthropic/{mdl}",
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
            cost_estimate=_cost(
                prompt_tokens, completion_tokens, self._input_rate, self._output_rate
            ),
            latency_ms=latency_ms,
        )


class FailoverClient:
    """Ordered provider chain (PRIMARY first). Fails over ONLY on transient errors.

    Tries each provider in order: on ``LLMError.retryable=True`` (429 / 5xx / timeout / transport)
    it moves to the next provider; on ``retryable=False`` (401 / 400) it RE-RAISES immediately —
    a misconfigured primary surfaces loudly, never masked. The returned ``LLMResponse.model``
    records which provider actually answered (observability). If every provider fails transiently,
    the last error is raised.
    """

    def __init__(self, providers: list[LLMClient]) -> None:
        if not providers:
            raise LLMError("FailoverClient requires at least one provider")
        self._providers = providers

    def complete(
        self,
        messages: list[LLMMessage],
        *,
        model: str | None = None,
        max_tokens: int | None = None,
        temperature: float = 0.0,
    ) -> LLMResponse:
        last: LLMError | None = None
        for i, provider in enumerate(self._providers):
            try:
                return provider.complete(
                    messages,
                    model=model,
                    max_tokens=max_tokens,
                    temperature=temperature,
                )
            except LLMError as exc:
                if not exc.retryable:
                    raise  # non-transient (auth/bad-request) → surface, do NOT fail over
                last = exc
                log.warning(
                    "llm.failover",
                    extra={
                        "extra": {
                            "provider_index": i,
                            "status_code": exc.status_code,
                            "has_next": i + 1 < len(self._providers),
                        }
                    },
                )
        assert last is not None  # only reached after ≥1 retryable failure
        raise last


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
    """Assemble the active LLM client: Anthropic PRIMARY + OpenRouter FALLBACK (specs/rag.md).

    Order of resolution:
    1. ``VIGIL_LLM_STUB=true`` → :class:`StubLLMClient` — returned FIRST, before any real client is
       constructed, so CI makes ZERO live calls to Anthropic OR OpenRouter and needs no keys.
    2. Anthropic (primary) is added when ``anthropic_enabled``; if it is enabled but its key is
       missing the read fails loud (no silent downgrade — an expected primary that vanished errors).
    3. OpenRouter (fallback) is appended.
    Returns the single client if only one is configured, else a :class:`FailoverClient`
    (transient-only failover; auth/bad-request surface).
    """
    settings = get_settings()
    if settings.llm_stub:
        return StubLLMClient()

    providers: list[LLMClient] = []
    if settings.anthropic_enabled:
        providers.append(
            AnthropicClient(
                api_key=settings.anthropic_api_key,  # fail-loud if primary key missing
                base_url=settings.anthropic_base_url,
                model=settings.anthropic_model,
                timeout_seconds=settings.llm_timeout_seconds,
                max_tokens=settings.llm_max_tokens,
                input_cost_per_1k_tokens=settings.anthropic_input_cost_per_1k_tokens,
                output_cost_per_1k_tokens=settings.anthropic_output_cost_per_1k_tokens,
            )
        )
    providers.append(
        OpenRouterClient(
            api_key=settings.llm_api_key,
            base_url=settings.llm_base_url,
            model=settings.llm_model,
            timeout_seconds=settings.llm_timeout_seconds,
            max_tokens=settings.llm_max_tokens,
            input_cost_per_1k_tokens=settings.llm_input_cost_per_1k_tokens,
            output_cost_per_1k_tokens=settings.llm_output_cost_per_1k_tokens,
        )
    )
    return providers[0] if len(providers) == 1 else FailoverClient(providers)
