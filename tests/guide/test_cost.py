"""Gate GUIDE-COST — the Guide tracks its OWN per-turn tokens/cost/latency in its OWN sink (hermetic).

No live call: httpx.post is monkeypatched for the client-parse tests; turns use stub/counting clients.
Proves: both clients parse provider usage (Anthropic input/output, OpenAI-compatible prompt/completion)
and default to 0 when absent; the stub is honest-zero; the split-rate cost arithmetic; a turn persists
the SUMMED (classify + answer) tokens + correct split-rate cost + model + latency; a blocked/refused
turn records only what ran; and GET /metrics/cost aggregates the sink (totals only — no rows/content).
Imports nothing from ``vigil.*``.
"""

from __future__ import annotations

import httpx
import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from guide.app import create_app
from guide.embeddings import get_embedder
from guide.llm import (
    AnthropicGuideClient,
    GuideLLMMessage,
    GuideLLMResponse,
    OpenRouterGuideClient,
    StubGuideLLMClient,
    compute_cost,
    get_guide_llm_client,
)
from guide.observability import (
    GuideMessageEvent,
    create_sink_engine,
    init_sink,
    write_message_event,
)
from guide.turn import run_guide_turn


@pytest.fixture()
def sink_engine(tmp_path):  # type: ignore[no-untyped-def]
    engine = create_sink_engine(f"sqlite+pysqlite:///{tmp_path / 'cost.db'}")
    init_sink(engine)
    return engine


def _reset_guide_config_cache() -> None:
    from guide.config import get_config

    get_config.cache_clear()


def _one_row(engine) -> GuideMessageEvent:  # type: ignore[no-untyped-def]
    with Session(engine) as s:
        rows = s.execute(select(GuideMessageEvent)).scalars().all()
    assert len(rows) == 1
    return rows[0]


class _FakeResp:
    def __init__(self, payload: dict) -> None:
        self._p = payload

    def raise_for_status(self) -> None:
        pass

    def json(self) -> dict:
        return self._p


class _CountingStub:
    """Returns KNOWN token counts + a chosen label/model for every call (records .calls)."""

    def __init__(
        self,
        *,
        label: str = "ON_TOPIC_GENERAL",
        prompt: int = 100,
        completion: int = 10,
        model: str = "anthropic/claude-haiku-4-5",
    ) -> None:
        self._label, self._p, self._c, self._m = label, prompt, completion, model
        self.calls: list = []

    def complete(self, messages):  # type: ignore[no-untyped-def]
        self.calls.append(messages)
        return GuideLLMResponse(
            content=self._label,
            model=self._m,
            latency_ms=7,
            prompt_tokens=self._p,
            completion_tokens=self._c,
        )


# --------------------------------------------------------------------------- client usage parsing
def test_anthropic_parses_input_output_tokens(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: _FakeResp(
            {
                "content": [{"text": "hi"}],
                "usage": {"input_tokens": 120, "output_tokens": 34},
            }
        ),
    )
    resp = AnthropicGuideClient(
        api_key="sk-ant-x",
        base_url="https://api.anthropic.com",
        model="claude-haiku-4-5",
        timeout_seconds=5.0,
        max_tokens=64,
    ).complete([GuideLLMMessage("user", "q")])
    assert resp.content == "hi"
    assert resp.prompt_tokens == 120 and resp.completion_tokens == 34


def test_openai_compatible_parses_prompt_completion_tokens(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        httpx,
        "post",
        lambda *a, **k: _FakeResp(
            {
                "choices": [{"message": {"content": "hi"}}],
                "usage": {"prompt_tokens": 90, "completion_tokens": 12},
            }
        ),
    )
    resp = OpenRouterGuideClient(
        api_key="k",
        base_url="https://openrouter.ai/api/v1",
        model="m",
        timeout_seconds=5.0,
        max_tokens=64,
    ).complete([GuideLLMMessage("user", "q")])
    assert resp.prompt_tokens == 90 and resp.completion_tokens == 12


def test_missing_usage_defaults_to_zero(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setattr(
        httpx, "post", lambda *a, **k: _FakeResp({"content": [{"text": "hi"}]})
    )
    resp = AnthropicGuideClient(
        api_key="sk-ant-x",
        base_url="https://api.anthropic.com",
        model="claude-haiku-4-5",
        timeout_seconds=5.0,
        max_tokens=64,
    ).complete([GuideLLMMessage("user", "q")])
    assert resp.prompt_tokens == 0 and resp.completion_tokens == 0


def test_stub_is_honest_zero_tokens() -> None:
    resp = StubGuideLLMClient().complete([GuideLLMMessage("user", "hi")])
    assert resp.prompt_tokens == 0 and resp.completion_tokens == 0
    assert resp.model == "stub/guide"


# --------------------------------------------------------------------------- cost arithmetic
def test_compute_cost_split_rates() -> None:
    assert compute_cost(1000, 500, 0.001, 0.005) == 0.0035  # 0.001 + 0.0025
    # output priced 5x input
    assert compute_cost(0, 1000, 0.001, 0.005) == 5 * compute_cost(
        1000, 0, 0.001, 0.005
    )


# --------------------------------------------------------------------------- turn persistence
def test_turn_persists_summed_tokens_cost_model_latency(
    monkeypatch, sink_engine, guide_index
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv(
        "VIGIL_GUIDE_LLM_PROVIDER", "anthropic"
    )  # real claude-haiku-4-5 rates
    _reset_guide_config_cache()
    try:
        llm = _CountingStub(label="ON_TOPIC_GENERAL", prompt=100, completion=10)
        result = run_guide_turn(
            "What models does Vigil use?",
            index=guide_index,
            embedder=get_embedder(),
            llm=llm,
            sink_engine=sink_engine,
        )
        assert result.guardrail_decision == "allowed"
        assert len(llm.calls) == 2  # classify + answer → tokens summed
        row = _one_row(sink_engine)
        assert row.prompt_tokens == 200 and row.completion_tokens == 20
        assert row.llm_provider_model == "anthropic/claude-haiku-4-5"
        assert row.latency_ms == 14  # 7 + 7
        # 200/1000*0.001 + 20/1000*0.005 = 0.0002 + 0.0001
        assert float(row.token_cost_estimate) == pytest.approx(0.0003)
    finally:
        _reset_guide_config_cache()


def test_blocked_turn_records_only_what_ran(
    monkeypatch, sink_engine, guide_index
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("VIGIL_GUIDE_LLM_PROVIDER", "anthropic")
    _reset_guide_config_cache()
    try:
        llm = _CountingStub(label="SPECIFIC_DATA", prompt=50, completion=4)
        result = run_guide_turn(
            "How many people dropped out of the Phase III cardiovascular study last month?",
            index=guide_index,
            embedder=get_embedder(),
            llm=llm,
            sink_engine=sink_engine,
        )
        assert result.guardrail_decision == "blocked"
        assert len(llm.calls) == 1  # only the classify ran — no answer call
        row = _one_row(sink_engine)
        assert row.prompt_tokens == 50 and row.completion_tokens == 4
        assert float(row.token_cost_estimate) == pytest.approx(
            compute_cost(50, 4, 0.001, 0.005)
        )
    finally:
        _reset_guide_config_cache()


def test_keyword_block_records_zero_cost(sink_engine, guide_index) -> None:  # type: ignore[no-untyped-def]
    # A participant keyword block fires at step 1 — BEFORE the classify — so no LLM call ran.
    llm = _CountingStub(label="ON_TOPIC_GENERAL", prompt=100, completion=10)
    result = run_guide_turn(
        "Show me B-0001's visit history.",
        index=guide_index,
        embedder=get_embedder(),
        llm=llm,
        sink_engine=sink_engine,
    )
    assert result.guardrail_decision == "blocked"
    assert len(llm.calls) == 0
    row = _one_row(sink_engine)
    assert row.prompt_tokens == 0 and row.completion_tokens == 0
    assert float(row.token_cost_estimate) == 0.0


def test_stub_turn_is_honest_zero_cost(sink_engine, guide_index) -> None:  # type: ignore[no-untyped-def]
    # Default config (provider openai_compatible, rate 0.0) + the deterministic stub (0 tokens).
    run_guide_turn(
        "What models does Vigil use?",
        index=guide_index,
        embedder=get_embedder(),
        llm=get_guide_llm_client(),
        sink_engine=sink_engine,
    )
    row = _one_row(sink_engine)
    assert row.prompt_tokens == 0 and row.completion_tokens == 0
    assert float(row.token_cost_estimate) == 0.0


# --------------------------------------------------------------------------- GET /metrics/cost
def _resources(sink_engine, guide_index):  # type: ignore[no-untyped-def]
    def _p():  # type: ignore[no-untyped-def]
        return sink_engine, guide_index, get_embedder(), get_guide_llm_client()

    return _p


def test_metrics_cost_aggregates_sink_totals_only(sink_engine, guide_index) -> None:  # type: ignore[no-untyped-def]
    with Session(sink_engine) as s:
        write_message_event(
            s,
            conversation_id="c1",
            request_id="r1",
            guardrail_decision="allowed",
            status="ok",
            llm_provider_model="anthropic/claude-haiku-4-5",
            latency_ms=100,
            token_cost_estimate=0.0003,
            prompt_tokens=200,
            completion_tokens=20,
        )
        write_message_event(
            s,
            conversation_id="c2",
            request_id="r2",
            guardrail_decision="allowed",
            status="ok",
            llm_provider_model="anthropic/claude-haiku-4-5",
            latency_ms=140,
            token_cost_estimate=0.0006,
            prompt_tokens=300,
            completion_tokens=30,
        )
        s.commit()

    client = TestClient(create_app(resources=_resources(sink_engine, guide_index)))
    r = client.get("/metrics/cost")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["surface"] == "public_guide"
    assert body["total_turns"] == 2
    assert body["total_prompt_tokens"] == 500
    assert body["total_completion_tokens"] == 50
    assert body["total_tokens"] == 550
    assert body["total_cost"] == pytest.approx(0.0009)
    assert body["avg_latency_ms"] == pytest.approx(120.0)
    bucket = next(
        b
        for b in body["rollups"]
        if b["llm_provider_model"] == "anthropic/claude-haiku-4-5"
    )
    assert bucket["turns"] == 2 and bucket["total_tokens"] == 550
    # AGGREGATE only — NO rows, NO question/answer content exposed.
    assert "redacted_user_msg" not in body and "redacted_assistant_msg" not in body
    for b in body["rollups"]:
        assert "redacted_user_msg" not in b and "redacted_assistant_msg" not in b


def test_metrics_cost_empty_sink_is_honest_zero(sink_engine, guide_index) -> None:  # type: ignore[no-untyped-def]
    client = TestClient(create_app(resources=_resources(sink_engine, guide_index)))
    body = client.get("/metrics/cost").json()
    assert body["total_turns"] == 0 and body["total_tokens"] == 0
    assert body["total_cost"] == 0.0 and body["avg_latency_ms"] == 0.0
    assert body["rollups"] == []
