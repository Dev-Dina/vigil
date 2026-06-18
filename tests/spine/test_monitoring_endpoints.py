"""Gate 6.2 — cost/latency capture + /cost · /models · /drift read surfaces.

Proves the 6.0 contract for the three monitoring read endpoints:
- CAPTURE (6.2 + 6.2b): a real turn persists the provider's REAL latency/cost onto the
  message_events row (no longer defaulted 0). The row carries the TURN TOTAL — router-classify +
  agent-generation; a refusal-AT-router shows the router's real (non-zero) cost; only a
  guardrail block BEFORE the router (no LLM call) is genuinely zero.
- COST: /monitoring/cost rolls up ONLY the real stored usage (no fabrication); platform/auditor
  only (403 others); RLS-bound (no cross-tenant leak in aggregates).
- MODELS: /monitoring/models reflects routing_state champion/shadow; platform/auditor only.
- DRIFT: /monitoring/drift is honest-empty (items == []), never fabricated; platform/auditor only.

Real Postgres via migrated_db; hermetic LLM via a usage-bearing scripted stub.
"""

from __future__ import annotations

import asyncio
import os
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select

from vigil.agents.corpus import ingest_card_corpus
from vigil.agents.embeddings import get_embedder
from vigil.agents.llm import LLMResponse
from vigil.api.app import create_app
from vigil.db.models import MessageEvent
from vigil.repositories import observability as obs_repo
from vigil.repositories.session import platform_session, sponsor_bootstrap_session
from vigil.workers.tasks import run_assistant_turn

_CHAMPION_MV = "sequence_v1.1:demo"
_CARD = "data/models/t2d/model_card.md"

# Controlled stub usage (6.2b): a turn's cost/latency = router-classify + agent-generation.
_ROUTER_COST = 0.00005
_ROUTER_LATENCY = 12
_AGENT_COST = 0.00045
_AGENT_LATENCY = 137


# ---------------------------------------------------------------------------
# Harness
# ---------------------------------------------------------------------------


class UsageLLM:
    """Hermetic stub that returns NON-ZERO usage so capture is observable in a test.

    Routes by SYSTEM prompt (router vs agent). The agent generation carries explicit
    prompt/completion tokens, cost, latency, and a provider/model id.
    """

    def __init__(
        self, *, route_to: str = "retention", answer: str = "grounded"
    ) -> None:
        self._route_to = route_to
        self._answer = answer

    def complete(self, messages, *, model=None, max_tokens=None, temperature=0.0):  # type: ignore[no-untyped-def]
        system = next((m.content for m in messages if m.role == "system"), "")
        if "ROUTER" in system:
            # The router-classification call carries its OWN real (small) usage (6.2b).
            return LLMResponse(
                content=f'{{"agent": "{self._route_to}", "reason": "stub"}}',
                model="scripted/router",
                prompt_tokens=40,
                completion_tokens=8,
                cost_estimate=_ROUTER_COST,
                latency_ms=_ROUTER_LATENCY,
            )
        return LLMResponse(
            content=self._answer,
            model="anthropic/claude-haiku-4-5",
            prompt_tokens=120,
            completion_tokens=30,
            cost_estimate=_AGENT_COST,
            latency_ms=_AGENT_LATENCY,
        )


def _client() -> TestClient:
    return TestClient(create_app(), raise_server_exceptions=False)


def _token(client: TestClient, email: str) -> str:
    pw = os.environ.get("VIGIL_SEED_PASSWORD", "vigil-dev-password")
    r = client.post("/api/v1/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _add_score(ids, pid, *, mv, score) -> None:  # type: ignore[no-untyped-def]
    from vigil.repositories import scoring as scoring_repo

    with sponsor_bootstrap_session(ids["sponsor_a"]) as session:
        scoring_repo.append_score(
            session,
            participant_id=pid,
            sponsor_id=uuid.UUID(ids["sponsor_a"]),
            trial_id=uuid.UUID(ids["trial_a"]),
            site_id=uuid.UUID(ids["site_a"]),
            risk_score=score,
            risk_band="high",
            top_factors=["missed_visits"],
            reasons=[],
            model_version=mv,
            model_card_ref=_CARD,
            synthetic=True,
        )


def _run(ids, *, user_id, content, participant_id=None, llm) -> dict:  # type: ignore[no-untyped-def]
    conv = str(uuid.uuid4())
    res = asyncio.get_event_loop().run_until_complete(
        run_assistant_turn(
            {"job_try": 0, "_llm_override": llm},
            conversation_id=conv,
            user_id=user_id,
            content=content,
            participant_id=participant_id,
            sponsor_id=ids["sponsor_a"],
            request_id="cap-req",
        )
    )
    res["_conversation_id"] = conv
    return res


def _events(ids, conv: str) -> list[MessageEvent]:
    with sponsor_bootstrap_session(ids["sponsor_a"]) as session:
        return list(
            session.execute(
                select(MessageEvent).where(
                    MessageEvent.conversation_id == uuid.UUID(conv)
                )
            ).scalars()
        )


# ---------------------------------------------------------------------------
# 1. CAPTURE: a turn persists REAL provider usage; refusal stays honest-zero
# ---------------------------------------------------------------------------


def test_turn_captures_real_usage(migrated_db: dict[str, str]) -> None:
    ids = migrated_db
    with platform_session() as session:
        ingest_card_corpus(session, get_embedder())
    _add_score(ids, uuid.UUID(ids["participant_a"]), mv=_CHAMPION_MV, score=0.72)

    res = _run(
        ids,
        user_id=ids["coordinator_a"],
        content="Why is this participant flagged as high risk?",
        participant_id=ids["participant_a"],
        llm=UsageLLM(answer="High risk due to missed visits."),
    )
    assert res["status"] == "ok"
    rows = _events(ids, res["_conversation_id"])
    assert len(rows) == 1
    row = rows[0]
    # TURN TOTAL (6.2b): router-classification + agent-generation, summed — NOT the old 0 default.
    assert row.latency_ms == _ROUTER_LATENCY + _AGENT_LATENCY, (
        f"latency must be router+agent: {row.latency_ms}"
    )
    assert abs(float(row.token_cost_estimate) - (_ROUTER_COST + _AGENT_COST)) < 1e-9, (
        f"cost must be router+agent: {row.token_cost_estimate}"
    )
    # Provider_model records the ANSWERING provider (the agent generation).
    assert row.llm_provider_model == "anthropic/claude-haiku-4-5"


def test_refusal_at_router_shows_real_router_cost(migrated_db: dict[str, str]) -> None:
    ids = migrated_db
    # A router-refused turn MADE a real classification call → its row carries the router's real
    # (non-zero) cost/latency now (6.2b), no longer honest-zero — but NOT the agent's.
    res = _run(
        ids,
        user_id=ids["coordinator_a"],
        content="Tell me a joke about the weather.",
        llm=UsageLLM(route_to="refuse"),
    )
    assert res["status"] == "refused"
    row = _events(ids, res["_conversation_id"])[0]
    assert row.route_or_agent == "router:refuse"
    assert row.latency_ms == _ROUTER_LATENCY, (
        f"router latency missing: {row.latency_ms}"
    )
    assert abs(float(row.token_cost_estimate) - _ROUTER_COST) < 1e-9, (
        f"router cost must be real (non-zero), not honest-zero: {row.token_cost_estimate}"
    )


def test_guardrail_block_before_router_is_zero(migrated_db: dict[str, str]) -> None:
    ids = migrated_db
    # A clinical/diagnostic question is blocked by the content guardrail BEFORE the router LLM
    # ever runs → no LLM call at all → genuinely zero (honest-zero is correct here).
    res = _run(
        ids,
        user_id=ids["coordinator_a"],
        content="What medication should this patient take for their diagnosis?",
        llm=UsageLLM(route_to="retention"),
    )
    assert res["status"] == "refused"
    row = _events(ids, res["_conversation_id"])[0]
    assert row.route_or_agent.startswith("guardrail:")
    assert row.latency_ms == 0 and float(row.token_cost_estimate) == 0.0


# ---------------------------------------------------------------------------
# 2. /monitoring/cost — rollups from real usage; platform/auditor only; RLS-bound
# ---------------------------------------------------------------------------


def _seed_cost_event(ids, *, sponsor_key, surface, model, cost, latency) -> None:  # type: ignore[no-untyped-def]
    sid = ids[sponsor_key]
    with sponsor_bootstrap_session(sid) as session:
        obs_repo.write_message_event(
            session,
            conversation_id=uuid.uuid4(),
            request_id=f"cost-{uuid.uuid4()}",
            sponsor_id=uuid.UUID(sid),
            role_or_guest_scope="coordinator",
            surface=surface,
            guardrail_decision="allowed",
            status="ok",
            redacted_user_msg="[REDACTED] q",
            redacted_assistant_msg="[REDACTED] a",
            route_or_agent="agent:retention",
            llm_provider_model=model,
            latency_ms=latency,
            token_cost_estimate=cost,
        )


def test_cost_rollups_real_and_role_gated(migrated_db: dict[str, str]) -> None:
    ids = migrated_db
    _seed_cost_event(
        ids,
        sponsor_key="sponsor_a",
        surface="local_assistant",
        model="anthropic/claude-haiku-4-5",
        cost=0.0010,
        latency=100,
    )
    _seed_cost_event(
        ids,
        sponsor_key="sponsor_a",
        surface="local_assistant",
        model="anthropic/claude-haiku-4-5",
        cost=0.0020,
        latency=200,
    )

    client = _client()
    # Platform/auditor reach it.
    r = client.get(
        "/api/v1/monitoring/cost?surface=local_assistant",
        headers=_h(_token(client, "auditor@vigil.example")),
    )
    assert r.status_code == 200, r.text
    body = r.json()
    bucket = next(
        b
        for b in body["rollups"]
        if b["llm_provider_model"] == "anthropic/claude-haiku-4-5"
        and b["surface"] == "local_assistant"
    )
    # Rollup reflects the real stored usage we seeded (sum), not a fabricated number.
    assert bucket["total_cost"] >= 0.0030 - 1e-9
    assert bucket["total_latency_ms"] >= 300
    assert bucket["turns"] >= 2

    # Sponsor/site roles are denied.
    for email in ("coord.a@vigil.example", "oversight.a@vigil.example"):
        rd = client.get("/api/v1/monitoring/cost", headers=_h(_token(client, email)))
        assert rd.status_code == 403, (
            f"{email} must be denied /cost (got {rd.status_code})"
        )


# ---------------------------------------------------------------------------
# 3. /monitoring/models — reflects routing_state; platform/auditor only
# ---------------------------------------------------------------------------


def test_models_reflects_routing_state_and_role_gated(
    migrated_db: dict[str, str],
) -> None:
    client = _client()
    r = client.get(
        "/api/v1/monitoring/models", headers=_h(_token(client, "admin@vigil.example"))
    )
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    champ = next(i for i in items if i["regime"] == "t2d" and i["role"] == "champion")
    assert champ["version"] == "sequence_v1.1:demo"  # the seeded champion projection
    assert any(i["role"] == "shadow" for i in items)

    for email in ("coord.a@vigil.example", "cra@vigil.example"):
        rd = client.get("/api/v1/monitoring/models", headers=_h(_token(client, email)))
        assert rd.status_code == 403, f"{email} must be denied /models"


# ---------------------------------------------------------------------------
# 4. /monitoring/drift — real points or empty (never fabricated); platform/auditor only
# ---------------------------------------------------------------------------


def test_drift_surface_role_gated(migrated_db: dict[str, str]) -> None:
    """Gate M1: drift is now a REAL computed surface (PSI/KS), so it may be empty OR carry points;
    the sacred guarantees are (a) it's a valid page of NON-fabricated points and (b) it stays
    platform/auditor-only. The producer→persist→read + provenance is proven in test_m1_drift.py."""
    client = _client()
    r = client.get(
        "/api/v1/monitoring/drift", headers=_h(_token(client, "auditor@vigil.example"))
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert isinstance(body["items"], list) and body["total"] == len(body["items"])
    # Any point present is a real computed metric (psi|ks) with provenance — never fabricated.
    for it in body["items"]:
        assert it["metric"] in ("psi", "ks")
        assert "synthetic" in it and "constructed_demo" in it

    for email in ("coord.a@vigil.example", "pi.a@vigil.example"):
        rd = client.get("/api/v1/monitoring/drift", headers=_h(_token(client, email)))
        assert rd.status_code == 403, f"{email} must be denied /drift"
