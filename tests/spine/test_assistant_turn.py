"""Gate 5.5 — router + Retention agent end-to-end turn (Phase-5 DONE-WHEN).

A turn flows: redact+guardrail -> router (LLM-classify / refuse) -> Retention agent (scoped tools
-> RAG -> cited answer) -> message_events row. The LLM is STUBBED (no live OpenRouter). Proves:
done-when happy path (in-scope answered WITH citations + event), refusals (guardrail + router),
scope isolation through the full turn (cross-tenant + cross-site), doc-grounding, champion-only,
grounded refusal.

Real Postgres via migrated_db; hermetic LLM via the scripted stub (conftest also forces
VIGIL_LLM_STUB=true).
"""

from __future__ import annotations

import asyncio
import uuid

from sqlalchemy import select, text

from vigil.agents.corpus import ingest_card_corpus
from vigil.agents.embeddings import get_embedder
from vigil.agents.llm import LLMResponse
from vigil.db.models import MessageEvent
from vigil.repositories.session import platform_session, sponsor_bootstrap_session
from vigil.workers.tasks import run_assistant_turn

_CHAMPION_MV = "sequence_v1.1:demo"
_SHADOW_MV = "structural_v1.0:t2d"
_CARD = "data/models/t2d/model_card.md"


class ScriptedLLM:
    """Hermetic LLM stub: routes by SYSTEM prompt (router vs agent) → canned output."""

    def __init__(
        self, *, route_to: str, answer: str = "[STUB] grounded answer"
    ) -> None:
        self._route_to = route_to  # "retention" | "report" | "operations" | "refuse"
        self._answer = answer
        self.calls: list[str] = []

    def complete(self, messages, *, model=None, max_tokens=None, temperature=0.0):  # type: ignore[no-untyped-def]
        system = next((m.content for m in messages if m.role == "system"), "")
        self.calls.append(system[:24])
        if "ROUTER" in system:
            content = f'{{"agent": "{self._route_to}", "reason": "stub"}}'
        else:  # RETENTION agent
            content = self._answer
        return LLMResponse(content=content, model="scripted/stub")


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
            risk_band="high" if score >= 0.7 else "low",
            top_factors=["missed_visits"],
            reasons=[],
            model_version=mv,
            model_card_ref=_CARD,
            synthetic=True,
        )


def _run(ids, *, user_id, content, participant_id=None, llm) -> dict:  # type: ignore[no-untyped-def]
    conv = str(uuid.uuid4())
    result = asyncio.get_event_loop().run_until_complete(
        run_assistant_turn(
            {"job_try": 0, "_llm_override": llm},
            conversation_id=conv,
            user_id=user_id,
            content=content,
            participant_id=participant_id,
            sponsor_id=ids["sponsor_a"],
            request_id="test-req",
        )
    )
    result["_conversation_id"] = conv
    return result


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
# 1. DONE-WHEN happy path: in-scope answered WITH citations + event written
# ---------------------------------------------------------------------------


def test_done_when_in_scope_answered_with_citations(
    migrated_db: dict[str, str],
) -> None:
    ids = migrated_db
    with platform_session() as session:
        ingest_card_corpus(session, get_embedder())
    _add_score(ids, uuid.UUID(ids["participant_a"]), mv=_CHAMPION_MV, score=0.72)

    llm = ScriptedLLM(
        route_to="retention", answer="PT is high risk due to missed visits."
    )
    res = _run(
        ids,
        user_id=ids["coordinator_a"],
        content="Why is this participant flagged as high risk?",
        participant_id=ids["participant_a"],
        llm=llm,
    )
    assert res["guardrail_decision"] == "allowed"
    assert res["status"] == "ok"
    # The routed agent is surfaced in the turn RESULT (not just message_events.route_or_agent).
    assert res["agent"] == "retention"
    assert res["citations"], "answered turn must carry citations (grounded)"
    # A structured (champion) citation for the in-scope participant is present.
    assert any(
        c["source_type"] == "structured" and ids["participant_a"] in c["source_id"]
        for c in res["citations"]
    )
    rows = _events(ids, res["_conversation_id"])
    assert len(rows) == 1 and rows[0].guardrail_decision == "allowed"
    assert rows[0].route_or_agent == "agent:retention"


# ---------------------------------------------------------------------------
# 2. Refusals: guardrail (clinical) + router-refuse — both blocked + event written
# ---------------------------------------------------------------------------


def test_clinical_question_blocked_by_guardrail(migrated_db: dict[str, str]) -> None:
    ids = migrated_db
    llm = ScriptedLLM(route_to="retention")  # should never be dispatched
    res = _run(
        ids,
        user_id=ids["coordinator_a"],
        content="What medication should this patient take for their diagnosis?",
        llm=llm,
    )
    assert res["guardrail_decision"] == "blocked" and res["status"] == "refused"
    assert res["agent"] is None, "a guardrail refusal dispatched no agent"
    assert not any("RETENTION" in c for c in llm.calls), (
        "agent must not run on a blocked turn"
    )
    rows = _events(ids, res["_conversation_id"])
    assert len(rows) == 1 and rows[0].guardrail_decision == "blocked"


def test_router_refuses_unclassifiable(migrated_db: dict[str, str]) -> None:
    ids = migrated_db
    llm = ScriptedLLM(route_to="refuse")
    res = _run(
        ids,
        user_id=ids["coordinator_a"],
        content="Tell me a joke about the weather.",
        llm=llm,
    )
    assert res["guardrail_decision"] == "blocked" and res["status"] == "refused"
    assert res["agent"] is None, "a router refusal dispatched no agent"
    rows = _events(ids, res["_conversation_id"])
    assert len(rows) == 1 and rows[0].route_or_agent == "router:refuse"


# ---------------------------------------------------------------------------
# 1b. The routed agent is surfaced END-TO-END in the AssistantTurn response schema
# ---------------------------------------------------------------------------


def test_assistant_turn_response_exposes_routed_agent(
    migrated_db: dict[str, str],
) -> None:
    """The AssistantTurn the client receives carries the routed agent (answered) / None (refusal).

    Proves the surfacing is end-to-end: worker result dict → ``_turn_from_result`` → AssistantTurn.
    The report/operations agents flow the same path; a refusal carries no agent.
    """
    from vigil.services.assistant_service import _turn_from_result

    ids = migrated_db
    with platform_session() as session:
        ingest_card_corpus(session, get_embedder())

    for route_to in ("report", "operations"):
        res = _run(
            ids,
            user_id=ids["coordinator_a"],
            content="How many high-risk participants are there?",
            llm=ScriptedLLM(route_to=route_to, answer="grounded"),
        )
        turn = _turn_from_result(res)
        assert turn.agent == route_to, (
            f"{route_to} turn must surface agent={route_to!r}"
        )

    # A router refusal → the response carries no agent.
    refusal = _run(
        ids,
        user_id=ids["coordinator_a"],
        content="Tell me a joke about the weather.",
        llm=ScriptedLLM(route_to="refuse"),
    )
    assert _turn_from_result(refusal).agent is None


# ---------------------------------------------------------------------------
# 3. Scope isolation THROUGH the full turn (cross-tenant + cross-site)
# ---------------------------------------------------------------------------


def test_turn_cross_tenant_no_participant_facts(migrated_db: dict[str, str]) -> None:
    ids = migrated_db
    with platform_session() as session:
        ingest_card_corpus(session, get_embedder())
    # coord.a asks about Sponsor B's participant → no structured facts may surface.
    llm = ScriptedLLM(route_to="retention", answer="answer")
    res = _run(
        ids,
        user_id=ids["coordinator_a"],
        content="Why is this participant flagged?",
        participant_id=ids["participant_b"],
        llm=llm,
    )
    assert not any(
        c["source_type"] == "structured" and ids["participant_b"] in c["source_id"]
        for c in res["citations"]
    ), "cross-tenant leak: Sponsor B participant facts surfaced through the turn"


def test_turn_cross_site_no_participant_facts(migrated_db: dict[str, str]) -> None:
    ids = migrated_db
    with platform_session() as session:
        ingest_card_corpus(session, get_embedder())
    # Build a SELF-CONTAINED sub-tenant under Sponsor A (new trial+site, NOT trial_a — so no
    # count pollution of the seeded trial). coord.a is bound to trial_a/site_a, so this
    # participant is out of their site/trial scope (axis-2) though sponsor-visible by RLS.
    from vigil.db.models import Participant, Site, Trial

    with sponsor_bootstrap_session(ids["sponsor_a"]) as session:
        t2 = Trial(sponsor_id=uuid.UUID(ids["sponsor_a"]), name="Turn probe trial 2")
        session.add(t2)
        session.flush()
        s2 = Site(
            sponsor_id=uuid.UUID(ids["sponsor_a"]),
            trial_id=t2.id,
            name="Turn probe site 2",
        )
        session.add(s2)
        session.flush()
        p2 = Participant(
            sponsor_id=uuid.UUID(ids["sponsor_a"]),
            trial_id=t2.id,
            site_id=s2.id,
            coded_ref="PT-TURN-S2",
            status="active",
            risk_score=0.9,
            risk_band="high",
        )
        session.add(p2)
        session.flush()
        p2_id = str(p2.id)

    llm = ScriptedLLM(route_to="retention", answer="answer")
    res = _run(
        ids,
        user_id=ids["coordinator_a"],  # bound to site_a, NOT s2
        content="Why is this participant flagged?",
        participant_id=p2_id,
        llm=llm,
    )
    assert not any(
        c["source_type"] == "structured" and p2_id in c["source_id"]
        for c in res["citations"]
    ), "cross-site leak: other-site participant facts surfaced through the turn"


# ---------------------------------------------------------------------------
# 4. Doc-grounding: a metric answer cites a card chunk (number not inlined)
# ---------------------------------------------------------------------------


def test_metric_question_grounded_in_card(migrated_db: dict[str, str]) -> None:
    ids = migrated_db
    with platform_session() as session:
        ingest_card_corpus(session, get_embedder())
    llm = ScriptedLLM(
        route_to="retention", answer="The sequence PR-AUC is reported in the card."
    )
    res = _run(
        ids,
        user_id=ids["coordinator_a"],
        content="What is the sequence model PR-AUC and lift over the structural baseline?",
        llm=llm,
    )
    assert res["citations"], "a metric answer must be grounded in retrieved card chunks"
    assert any(
        c["source_type"] == "document" and c["source_id"].endswith(".md")
        for c in res["citations"]
    ), "metric must be grounded in a card-corpus document citation, not inlined"


# ---------------------------------------------------------------------------
# 5. Champion-only via the agent + grounded refusal
# ---------------------------------------------------------------------------


def test_agent_surfaces_champion_not_shadow(migrated_db: dict[str, str]) -> None:
    ids = migrated_db
    with platform_session() as session:
        ingest_card_corpus(session, get_embedder())
    pid = uuid.UUID(ids["participant_a"])
    _add_score(ids, pid, mv=_CHAMPION_MV, score=0.66)
    _add_score(ids, pid, mv=_SHADOW_MV, score=0.99)  # adversarial shadow

    llm = ScriptedLLM(route_to="retention", answer="grounded")
    res = _run(
        ids,
        user_id=ids["coordinator_a"],
        content="Why is this participant flagged?",
        participant_id=ids["participant_a"],
        llm=llm,
    )
    structured = [c for c in res["citations"] if c["source_type"] == "structured"]
    assert structured, "expected a champion structured citation"
    assert all(_CHAMPION_MV in c["locator"] for c in structured), (
        "shadow model_version must never appear in the agent's citations"
    )
    assert all(_SHADOW_MV not in c["locator"] for c in structured)


def test_grounded_refusal_when_nothing_retrieved(migrated_db: dict[str, str]) -> None:
    ids = migrated_db
    # No card corpus ingested for THIS conversation's retrieval target + no participant → the
    # agent has no grounded context. With an empty corpus the agent must refuse, not hallucinate.
    with platform_session() as session:
        session.execute(text("DELETE FROM document_chunk"))
    llm = ScriptedLLM(route_to="retention", answer="SHOULD NOT BE USED")
    res = _run(
        ids,
        user_id=ids["coordinator_a"],
        content="Explain the retention methodology in general terms.",
        llm=llm,
    )
    assert res["status"] == "refused", "no grounded context → grounded refusal"
    assert "grounded information" in res["content"].lower()


# ---------------------------------------------------------------------------
# 6. Endpoint wiring: POST messages enqueues (202 + job_id), never inline
# ---------------------------------------------------------------------------


def test_assistant_endpoints_enqueue_202(migrated_db: dict[str, str]) -> None:
    import os

    from fastapi.testclient import TestClient

    from vigil.api.app import create_app

    client = TestClient(create_app(), raise_server_exceptions=False)
    pw = os.environ.get("VIGIL_SEED_PASSWORD", "vigil-dev-password")
    r = client.post(
        "/api/v1/auth/login", json={"email": "coord.a@vigil.example", "password": pw}
    )
    assert r.status_code == 200, r.text
    token = r.json()["access_token"]
    h = {"Authorization": f"Bearer {token}"}

    conv = client.post("/api/v1/assistant/conversations", headers=h)
    assert conv.status_code == 200, conv.text
    conversation_id = conv.json()["conversation_id"]

    msg = client.post(
        f"/api/v1/assistant/conversations/{conversation_id}/messages",
        json={"content": "Why is this participant flagged?"},
        headers=h,
    )
    assert msg.status_code == 202, msg.text  # enqueued, never inline
    body = msg.json()
    assert body["job_id"] and body["conversation_id"] == conversation_id
    assert body["status"] == "queued"
