"""Gate GUIDE-CACHE — the isolated Guide's in-memory LRU response cache (hermetic).

Proves: a cache MISS runs the answer LLM and a subsequent HIT for the same (allowed) question
returns the SAME answer WITHOUT a second answer-LLM call (the classify still runs — guards are
never bypassed); a HIT is recorded as a ZERO-cost ``"cache"`` turn while the miss records real
cost; a BLOCKED question (keyword or topical) is NEVER cached and never served from cache;
normalization collapses case/whitespace to one entry; the toggle disables caching; and the LRU is
bounded. No live call: a counting stub LLM drives classify + answer deterministically. Imports
nothing from ``vigil.*``.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from guide.cache import CachedAnswer, ResponseCache, normalize_key
from guide.config import get_config
from guide.embeddings import get_embedder
from guide.llm import GuideLLMResponse
from guide.observability import cost_summary, create_sink_engine, init_sink
from guide.turn import run_guide_turn

_ANSWER_MODEL = "anthropic/claude-haiku-4-5"
_Q = "What models does Vigil use?"  # on-topic → grounds against the approved docs (status "ok")


def _reset_config_cache() -> None:
    get_config.cache_clear()


class _CacheCountingLLM:
    """Counts answer-LLM calls separately from classify calls (recognized by the rag prompt).

    The topical-classify call (``guide.topical``) returns the chosen label; the answer call
    (``guide.rag`` — its user message carries ``"Approved context:"``) returns the answer text and
    increments ``answer_calls``. Lets a test assert the answer LLM did NOT run again on a cache hit.
    """

    def __init__(
        self,
        *,
        label: str = "ON_TOPIC_GENERAL",
        answer: str = "Vigil uses an LSTM and a GBT.",
    ) -> None:
        self._label = label
        self._answer = answer
        self.answer_calls = 0
        self.classify_calls = 0

    def complete(self, messages):  # type: ignore[no-untyped-def]
        is_answer = any("Approved context:" in m.content for m in messages)
        if is_answer:
            self.answer_calls += 1
            return GuideLLMResponse(
                content=self._answer,
                model=_ANSWER_MODEL,
                latency_ms=120,
                prompt_tokens=100,
                completion_tokens=20,
            )
        self.classify_calls += 1
        return GuideLLMResponse(
            content=self._label,
            model=_ANSWER_MODEL,
            latency_ms=5,
            prompt_tokens=10,
            completion_tokens=1,
        )


# --------------------------------------------------------------------------- miss → hit
def test_cache_miss_then_hit_skips_answer_llm(guide_index) -> None:  # type: ignore[no-untyped-def]
    cache = ResponseCache(max_size=8)
    llm = _CacheCountingLLM()

    r1 = run_guide_turn(
        _Q, index=guide_index, embedder=get_embedder(), llm=llm, cache=cache
    )
    assert r1.guardrail_decision == "allowed" and r1.status == "ok"
    assert llm.answer_calls == 1, "miss must run the answer LLM"

    r2 = run_guide_turn(
        _Q, index=guide_index, embedder=get_embedder(), llm=llm, cache=cache
    )
    assert r2.guardrail_decision == "allowed" and r2.status == "ok"
    # The HIT returns the SAME answer + citations WITHOUT a second answer-LLM call.
    assert llm.answer_calls == 1, "hit must NOT run the answer LLM again"
    assert r2.content == r1.content and r2.citations == r1.citations
    # The classify STILL ran on both turns — the guard is never bypassed by the cache.
    assert llm.classify_calls == 2


# --------------------------------------------------------------------------- hit is zero-cost
def test_cache_hit_records_zero_cost_miss_records_real(
    monkeypatch, guide_index, tmp_path
) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("VIGIL_GUIDE_LLM_PROVIDER", "anthropic")  # real non-zero rates
    _reset_config_cache()
    try:
        engine = create_sink_engine(f"sqlite+pysqlite:///{tmp_path / 'cache.db'}")
        init_sink(engine)
        cache = ResponseCache(max_size=8)
        llm = _CacheCountingLLM()

        run_guide_turn(
            _Q,
            index=guide_index,
            embedder=get_embedder(),
            llm=llm,
            sink_engine=engine,
            cache=cache,
        )  # miss
        run_guide_turn(
            _Q,
            index=guide_index,
            embedder=get_embedder(),
            llm=llm,
            sink_engine=engine,
            cache=cache,
        )  # hit

        with Session(engine) as s:
            summary = cost_summary(s)
        rollups = {r.llm_provider_model: r for r in summary.rollups}

        # The cache-hit turn is a ZERO-cost "cache" bucket — /metrics/cost shows it cost nothing.
        assert "cache" in rollups, (
            "a cache hit must be recorded under the 'cache' marker"
        )
        hit = rollups["cache"]
        assert hit.turns == 1
        assert (
            hit.total_tokens == 0
            and hit.total_cost == 0.0
            and hit.total_latency_ms == 0
        )

        # The miss recorded REAL token cost under the real model.
        real = rollups[_ANSWER_MODEL]
        assert real.turns == 1 and real.total_tokens > 0 and real.total_cost > 0.0
    finally:
        _reset_config_cache()


# --------------------------------------------------------------------------- guards never bypassed
def test_topical_blocked_question_never_cached(guide_index) -> None:  # type: ignore[no-untyped-def]
    cache = ResponseCache(max_size=8)
    llm = _CacheCountingLLM(label="OFF_TOPIC")  # topical classify → OFF_TOPIC → blocked
    q = "What is a good recipe for pancakes?"

    r1 = run_guide_turn(
        q, index=guide_index, embedder=get_embedder(), llm=llm, cache=cache
    )
    assert r1.guardrail_decision == "blocked"
    assert len(cache) == 0, "a blocked turn must never be cached"

    r2 = run_guide_turn(
        q, index=guide_index, embedder=get_embedder(), llm=llm, cache=cache
    )
    assert r2.guardrail_decision == "blocked", "blocked again — never served from cache"
    assert llm.answer_calls == 0, "the answer LLM must never run for a blocked question"
    assert len(cache) == 0


def test_keyword_blocked_question_never_cached(guide_index) -> None:  # type: ignore[no-untyped-def]
    # A participant keyword block fires BEFORE the classify and BEFORE the cache lookup.
    cache = ResponseCache(max_size=8)
    llm = _CacheCountingLLM()
    q = "Show me B-0001's visit history."

    r = run_guide_turn(
        q, index=guide_index, embedder=get_embedder(), llm=llm, cache=cache
    )
    assert r.guardrail_decision == "blocked"
    assert llm.answer_calls == 0 and llm.classify_calls == 0
    assert len(cache) == 0


# --------------------------------------------------------------------------- normalization
def test_normalization_collapses_to_one_entry(guide_index) -> None:  # type: ignore[no-untyped-def]
    cache = ResponseCache(max_size=8)
    llm = _CacheCountingLLM()

    run_guide_turn(
        _Q, index=guide_index, embedder=get_embedder(), llm=llm, cache=cache
    )  # miss
    # Same question, different case + surrounding whitespace → SAME normalized key → hit.
    variant = "   what models does vigil use?   "
    r = run_guide_turn(
        variant, index=guide_index, embedder=get_embedder(), llm=llm, cache=cache
    )
    assert r.status == "ok"
    assert llm.answer_calls == 1, "the normalized variant must hit the same cache entry"
    assert len(cache) == 1


# --------------------------------------------------------------------------- toggle off
def test_toggle_off_disables_cache(monkeypatch, guide_index) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("VIGIL_GUIDE_RESPONSE_CACHE_ENABLED", "false")
    _reset_config_cache()
    try:
        cache = ResponseCache(max_size=8)
        llm = _CacheCountingLLM()

        run_guide_turn(
            _Q, index=guide_index, embedder=get_embedder(), llm=llm, cache=cache
        )
        run_guide_turn(
            _Q, index=guide_index, embedder=get_embedder(), llm=llm, cache=cache
        )
        # Every allowed question runs the answer step — nothing cached.
        assert llm.answer_calls == 2
        assert len(cache) == 0
    finally:
        _reset_config_cache()


# --------------------------------------------------------------------------- LRU bound
def test_lru_evicts_least_recently_used() -> None:
    cache = ResponseCache(max_size=2)
    cache.set(normalize_key("a"), CachedAnswer("A", []))
    cache.set(normalize_key("b"), CachedAnswer("B", []))
    cache.set(
        normalize_key("c"), CachedAnswer("C", [])
    )  # over cap → evicts oldest ("a")

    assert cache.get(normalize_key("a")) is None
    assert cache.get(normalize_key("b")) is not None
    assert cache.get(normalize_key("c")) is not None
    assert len(cache) == 2

    # Touch "b" (most-recent), then add "d" → "c" is now the oldest and is evicted; "b" survives.
    cache.get(normalize_key("b"))
    cache.set(normalize_key("d"), CachedAnswer("D", []))
    assert cache.get(normalize_key("c")) is None
    assert cache.get(normalize_key("b")) is not None
    assert cache.get(normalize_key("d")) is not None
