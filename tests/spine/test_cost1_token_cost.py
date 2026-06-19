"""Gate COST-1 — real per-turn token persistence + real (split-rate) dollar cost.

Proves:
- The cost FORMULA is split input/output (Anthropic prices output ~5× input): the arithmetic is
  input_tokens×input_rate + output_tokens×output_rate, and the configured Anthropic rate is the
  REAL claude-haiku-4-5 list price ($1.00/$5.00 per 1M → 0.001/0.005 per 1k).
- A turn (fake client returning KNOWN token counts) PERSISTS prompt_tokens + completion_tokens on
  the message_events row, summed across the router-classification + agent-generation calls, and the
  stored token_cost_estimate is the correct split-rate product — a real (small) non-zero cost.
- The deterministic stub stays honest-zero cost.
- GET /monitoring/cost aggregates the REAL stored token totals + cost from rows (no fabrication),
  platform/auditor-only (tenant 403).

Real Postgres via migrated_db; hermetic (no live LLM — the turn uses an injected fake client).
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select

from vigil.agents.corpus import ingest_card_corpus
from vigil.agents.embeddings import get_embedder
from vigil.agents.llm import LLMResponse, StubLLMClient, _cost
from vigil.api.app import create_app
from vigil.core.config import get_settings
from vigil.db.models import MessageEvent
from vigil.repositories import observability as obs_repo
from vigil.repositories.session import platform_session, sponsor_bootstrap_session
from vigil.workers.tasks import run_assistant_turn

_CHAMPION_MV = "sequence_v1.1:demo"
_CARD = "data/models/t2d/model_card.md"
_HAIKU = "anthropic/claude-haiku-4-5"


def _loop() -> asyncio.AbstractEventLoop:
    try:
        return asyncio.get_event_loop()
    except RuntimeError:
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        return loop


# ---------------------------------------------------------------------------
# 1. Cost arithmetic — split input/output rates; real haiku price configured
# ---------------------------------------------------------------------------


def test_cost_is_split_input_output_at_real_rate() -> None:
    # Pure arithmetic: input_tokens×input_rate + output_tokens×output_rate.
    assert _cost(1000, 500, 0.001, 0.005) == 0.0035  # 0.001 + 0.0025
    # Output priced 5× input → an output token costs 5× an input token (NOT a blended rate).
    assert _cost(0, 1000, 0.001, 0.005) == 5 * _cost(1000, 0, 0.001, 0.005)

    s = get_settings()
    # The configured Anthropic rate is the REAL claude-haiku-4-5 list price (claude-api skill
    # model table): $1.00 / 1M input, $5.00 / 1M output → 0.001 / 0.005 per 1k.
    assert s.anthropic_input_cost_per_1k_tokens == 0.001
    assert s.anthropic_output_cost_per_1k_tokens == 0.005
    assert (
        _cost(
            2000,
            1000,
            s.anthropic_input_cost_per_1k_tokens,
            s.anthropic_output_cost_per_1k_tokens,
        )
        == 0.007
    )  # 2000/1000*0.001 + 1000/1000*0.005


# ---------------------------------------------------------------------------
# 2. The deterministic stub stays honest-zero cost
# ---------------------------------------------------------------------------


def test_stub_client_is_honest_zero_cost() -> None:
    from vigil.agents.llm import LLMMessage

    resp = StubLLMClient().complete([LLMMessage("user", "hello there friend")])
    assert resp.cost_estimate == 0.0, "the stub must never claim a dollar cost"
    # The stub's pseudo token counts are populated (deterministic) but clearly not a real provider.
    assert resp.model == "stub/deterministic"


# ---------------------------------------------------------------------------
# 3. A turn PERSISTS real per-turn tokens + the correct split-rate cost
# ---------------------------------------------------------------------------


class _CountingLLM:
    """Fake client returning KNOWN, DISTINCT token counts for the router vs the agent call, with a
    cost computed by the REAL split-rate formula — so the persisted row is exactly assertable."""

    def __init__(self, *, router_tokens, agent_tokens):  # type: ignore[no-untyped-def]
        self.router_tokens = router_tokens  # (prompt, completion)
        self.agent_tokens = agent_tokens
        s = get_settings()
        self._ir = s.anthropic_input_cost_per_1k_tokens
        self._or = s.anthropic_output_cost_per_1k_tokens

    def complete(self, messages, *, model=None, max_tokens=None, temperature=0.0):  # type: ignore[no-untyped-def]
        system = next((m.content for m in messages if m.role == "system"), "")
        if "ROUTER" in system:
            p, c = self.router_tokens
            content = '{"agent": "retention", "reason": "stub"}'
        else:
            p, c = self.agent_tokens
            content = "Grounded answer from the card."
        return LLMResponse(
            content=content,
            model=_HAIKU,
            prompt_tokens=p,
            completion_tokens=c,
            cost_estimate=_cost(p, c, self._ir, self._or),
            latency_ms=7,
        )


def test_turn_persists_real_tokens_and_split_rate_cost(
    migrated_db: dict[str, str],
) -> None:
    ids = migrated_db
    with platform_session() as session:
        ingest_card_corpus(session, get_embedder())
    # A champion score so the agent has a structured fact → it generates (calls the LLM).
    from vigil.repositories import scoring as scoring_repo

    with sponsor_bootstrap_session(ids["sponsor_a"]) as session:
        scoring_repo.append_score(
            session,
            participant_id=uuid.UUID(ids["participant_a"]),
            sponsor_id=uuid.UUID(ids["sponsor_a"]),
            trial_id=uuid.UUID(ids["trial_a"]),
            site_id=uuid.UUID(ids["site_a"]),
            risk_score=0.72,
            risk_band="high",
            top_factors=["missed_visits"],
            reasons=[],
            model_version=_CHAMPION_MV,
            model_card_ref=_CARD,
            synthetic=True,
        )

    router_tokens = (1200, 30)
    agent_tokens = (2000, 400)
    llm = _CountingLLM(router_tokens=router_tokens, agent_tokens=agent_tokens)

    conv = str(uuid.uuid4())
    res = _loop().run_until_complete(
        run_assistant_turn(
            {"job_try": 0, "_llm_override": llm},
            conversation_id=conv,
            user_id=ids["coordinator_a"],
            content="Why is this participant flagged as high risk?",
            participant_id=ids["participant_a"],
            sponsor_id=ids["sponsor_a"],
            request_id="cost1-req",
        )
    )
    assert res["guardrail_decision"] == "allowed" and res["status"] == "ok"

    with sponsor_bootstrap_session(ids["sponsor_a"]) as session:
        row = session.execute(
            select(MessageEvent).where(MessageEvent.conversation_id == uuid.UUID(conv))
        ).scalar_one()

    # REAL token counts persisted, SUMMED across the router + agent calls.
    assert row.prompt_tokens == router_tokens[0] + agent_tokens[0] == 3200
    assert row.completion_tokens == router_tokens[1] + agent_tokens[1] == 430

    # The stored dollar cost is the correct split-rate product (router + agent), NON-ZERO.
    s = get_settings()
    expected = round(
        _cost(
            *router_tokens,
            s.anthropic_input_cost_per_1k_tokens,
            s.anthropic_output_cost_per_1k_tokens,
        )
        + _cost(
            *agent_tokens,
            s.anthropic_input_cost_per_1k_tokens,
            s.anthropic_output_cost_per_1k_tokens,
        ),
        6,
    )
    assert expected > 0.0
    assert float(row.token_cost_estimate) == pytest.approx(expected)

    # Clean up the ingested card-corpus chunks so this test does not pollute the cross-tenant
    # document-retrieval suite (the session-scoped DB is shared; mirrors test_assistant_turn).
    from sqlalchemy import text

    with platform_session() as session:
        session.execute(text("DELETE FROM document_chunk"))


# ---------------------------------------------------------------------------
# 4. GET /monitoring/cost aggregates real token totals + cost; tenant 403
# ---------------------------------------------------------------------------


def _client() -> TestClient:
    return TestClient(create_app(), raise_server_exceptions=False)


def _token(client: TestClient, email: str) -> str:
    pw = os.environ.get("VIGIL_SEED_PASSWORD", "vigil-dev-password")
    r = client.post("/api/v1/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _seed_event(ids, *, model, prompt_tokens, completion_tokens, cost) -> None:  # type: ignore[no-untyped-def]
    with sponsor_bootstrap_session(ids["sponsor_a"]) as session:
        obs_repo.write_message_event(
            session,
            conversation_id=uuid.uuid4(),
            request_id=f"cost1-{uuid.uuid4()}",
            sponsor_id=uuid.UUID(ids["sponsor_a"]),
            role_or_guest_scope="coordinator",
            surface="local_assistant",
            guardrail_decision="allowed",
            status="ok",
            redacted_user_msg="[REDACTED] q",
            redacted_assistant_msg="[REDACTED] a",
            route_or_agent="agent:retention",
            llm_provider_model=model,
            latency_ms=120,
            token_cost_estimate=cost,
            prompt_tokens=prompt_tokens,
            completion_tokens=completion_tokens,
        )


def test_monitoring_cost_token_totals_and_role_gate(
    migrated_db: dict[str, str],
) -> None:
    ids = migrated_db
    # Isolate by a UNIQUE provider-model so the (surface, model) rollup bucket is EXACTLY our two
    # rows — robust against the session-scoped DB accumulating other tests' events (and against any
    # client/DB clock skew that a time-window filter would be vulnerable to).
    model = f"anthropic/claude-haiku-4-5#cost1-{uuid.uuid4().hex[:8]}"
    _seed_event(ids, model=model, prompt_tokens=100, completion_tokens=10, cost=0.0006)
    _seed_event(ids, model=model, prompt_tokens=200, completion_tokens=20, cost=0.0012)

    client = _client()
    r = client.get(
        "/api/v1/monitoring/cost",
        params={"surface": "local_assistant"},
        headers=_h(_token(client, "auditor@vigil.example")),
    )
    assert r.status_code == 200, r.text
    body = r.json()

    # The rollup bucket for our unique model is EXACTLY the two stored rows — summed, not fabricated.
    bucket = next(b for b in body["rollups"] if b["llm_provider_model"] == model)
    assert bucket["surface"] == "local_assistant"
    assert bucket["turns"] == 2
    assert bucket["prompt_tokens"] == 300
    assert bucket["completion_tokens"] == 20 + 10 == 30
    assert bucket["total_tokens"] == 330
    assert bucket["total_cost"] == pytest.approx(0.0018)

    # Report-level totals AGGREGATE real stored token usage (>= our two rows; never fabricated).
    assert body["total_prompt_tokens"] >= 300
    assert body["total_completion_tokens"] >= 30
    assert body["total_tokens"] >= 330
    assert body["total_cost"] >= 0.0018 - 1e-9

    # Tenant/site role is denied the cost surface.
    rd = client.get(
        "/api/v1/monitoring/cost",
        params={"surface": "local_assistant"},
        headers=_h(_token(client, "coord.a@vigil.example")),
    )
    assert rd.status_code == 403, rd.text
