"""Gate 5.6 — Report + Operations agents on the SAME spine: dual-axis isolation across ALL agents.

Drives full turns (router → agent) routed to ``report`` and ``operations`` and re-proves the 5.4
sacred guarantee END-TO-END for each: a turn cannot surface another sponsor's (cross-tenant) or
another site's (cross-site within tenant) participant facts, champion-only holds, and an in-scope
turn answers with citations + writes a message_events row tagged with the right agent.

LLM is STUBBED (scripted; no live call). Real Postgres via migrated_db.
"""

from __future__ import annotations

import asyncio
import uuid

import pytest
from sqlalchemy import select

from vigil.agents.corpus import ingest_card_corpus
from vigil.agents.embeddings import get_embedder
from vigil.agents.llm import LLMResponse
from vigil.db.models import MessageEvent, Participant, Site, Trial
from vigil.repositories.session import platform_session, sponsor_bootstrap_session
from vigil.workers.tasks import run_assistant_turn

_CHAMPION_MV = "sequence_v1.0:demo"
_SHADOW_MV = "structural_v1.0:t2d"
_CARD = "data/models/t2d/model_card.md"


class ScriptedLLM:
    """Hermetic stub: ROUTER call → route to `route_to`; agent call → canned answer."""

    def __init__(self, *, route_to: str, answer: str = "grounded answer") -> None:
        self._route_to = route_to
        self._answer = answer

    def complete(self, messages, *, model=None, max_tokens=None, temperature=0.0):  # type: ignore[no-untyped-def]
        system = next((m.content for m in messages if m.role == "system"), "")
        if "ROUTER" in system:
            return LLMResponse(
                content=f'{{"agent": "{self._route_to}", "reason": "stub"}}',
                model="scripted/stub",
            )
        return LLMResponse(content=self._answer, model="scripted/stub")


def _add_champion_score(
    ids, pid, *, trial_id, site_id, mv=_CHAMPION_MV, score=0.7
) -> None:  # type: ignore[no-untyped-def]
    from vigil.repositories import scoring as scoring_repo

    with sponsor_bootstrap_session(ids["sponsor_a"]) as session:
        scoring_repo.append_score(
            session,
            participant_id=pid,
            sponsor_id=uuid.UUID(ids["sponsor_a"]),
            trial_id=trial_id,
            site_id=site_id,
            risk_score=score,
            risk_band="high" if score >= 0.7 else "low",
            top_factors=["missed_visits"],
            reasons=[],
            model_version=mv,
            model_card_ref=_CARD,
            synthetic=True,
        )


def _run(ids, *, user_id, content, participant_id, route_to) -> dict:  # type: ignore[no-untyped-def]
    conv = str(uuid.uuid4())
    res = asyncio.get_event_loop().run_until_complete(
        run_assistant_turn(
            {"job_try": 0, "_llm_override": ScriptedLLM(route_to=route_to)},
            conversation_id=conv,
            user_id=user_id,
            content=content,
            participant_id=participant_id,
            sponsor_id=ids["sponsor_a"],
            request_id="test-req",
        )
    )
    res["_conversation_id"] = conv
    return res


def _events(ids, conv) -> list[MessageEvent]:  # type: ignore[no-untyped-def]
    with sponsor_bootstrap_session(ids["sponsor_a"]) as session:
        return list(
            session.execute(
                select(MessageEvent).where(
                    MessageEvent.conversation_id == uuid.UUID(conv)
                )
            ).scalars()
        )


def _second_site_participant(ids) -> str:  # type: ignore[no-untyped-def]
    """A participant under a NEW trial/site of Sponsor A (out of coord.a's site scope; no
    seed-count pollution of trial_a). Champion-scored so only SITE scope can hide it."""
    sponsor_a = uuid.UUID(ids["sponsor_a"])
    with sponsor_bootstrap_session(ids["sponsor_a"]) as session:
        t2 = Trial(sponsor_id=sponsor_a, name="5.6 probe trial")
        session.add(t2)
        session.flush()
        s2 = Site(sponsor_id=sponsor_a, trial_id=t2.id, name="5.6 probe site")
        session.add(s2)
        session.flush()
        p2 = Participant(
            sponsor_id=sponsor_a,
            trial_id=t2.id,
            site_id=s2.id,
            coded_ref="PT-56-S2",
            status="active",
            risk_score=0.9,
            risk_band="high",
        )
        session.add(p2)
        session.flush()
        pid, tid, sid = p2.id, t2.id, s2.id
    _add_champion_score(ids, pid, trial_id=tid, site_id=sid)
    return str(pid)


_AGENTS = ["report", "operations"]


@pytest.fixture(autouse=True)
def _corpus(migrated_db):  # type: ignore[no-untyped-def]
    with platform_session() as session:
        ingest_card_corpus(session, get_embedder())
    return migrated_db


# ---------------------------------------------------------------------------
# Happy path: in-scope answered with citations + correctly-tagged event (per agent)
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("agent", _AGENTS)
def test_agent_in_scope_answered_with_citations(migrated_db, agent) -> None:  # type: ignore[no-untyped-def]
    ids = migrated_db
    _add_champion_score(
        ids,
        uuid.UUID(ids["participant_a"]),
        trial_id=uuid.UUID(ids["trial_a"]),
        site_id=uuid.UUID(ids["site_a"]),
    )
    res = _run(
        ids,
        user_id=ids["coordinator_a"],
        content="Summarise the cohort risk picture.",
        participant_id=ids["participant_a"],
        route_to=agent,
    )
    assert res["guardrail_decision"] == "allowed" and res["status"] == "ok"
    assert res["citations"], "answered turn must be grounded/cited"
    rows = _events(ids, res["_conversation_id"])
    assert len(rows) == 1 and rows[0].route_or_agent == f"agent:{agent}"


# ---------------------------------------------------------------------------
# Axis 1 — cross-tenant: a Report/Operations turn must not surface Sponsor B facts
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("agent", _AGENTS)
def test_agent_cross_tenant_no_facts(migrated_db, agent) -> None:  # type: ignore[no-untyped-def]
    ids = migrated_db
    res = _run(
        ids,
        user_id=ids["coordinator_a"],
        content="Report on this participant.",
        participant_id=ids["participant_b"],
        route_to=agent,
    )
    assert not any(
        c["source_type"] == "structured" and ids["participant_b"] in c["source_id"]
        for c in res["citations"]
    ), f"{agent}: cross-tenant leak — Sponsor B facts surfaced through the turn"


# ---------------------------------------------------------------------------
# Axis 2 — cross-site within a tenant: site-scoped coord cannot reach another site
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("agent", _AGENTS)
def test_agent_cross_site_no_facts(migrated_db, agent) -> None:  # type: ignore[no-untyped-def]
    ids = migrated_db
    other_site_pid = _second_site_participant(ids)
    res = _run(
        ids,
        user_id=ids["coordinator_a"],  # bound to site_a only
        content="Report on this participant.",
        participant_id=other_site_pid,
        route_to=agent,
    )
    assert not any(
        c["source_type"] == "structured" and other_site_pid in c["source_id"]
        for c in res["citations"]
    ), f"{agent}: cross-site leak — other-site facts surfaced through the turn"


# ---------------------------------------------------------------------------
# Champion-only via the agent path: shadow never appears in citations
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("agent", _AGENTS)
def test_agent_champion_only(migrated_db, agent) -> None:  # type: ignore[no-untyped-def]
    ids = migrated_db
    pid = uuid.UUID(ids["participant_a"])
    ta, sa = uuid.UUID(ids["trial_a"]), uuid.UUID(ids["site_a"])
    _add_champion_score(ids, pid, trial_id=ta, site_id=sa, mv=_CHAMPION_MV, score=0.66)
    _add_champion_score(
        ids, pid, trial_id=ta, site_id=sa, mv=_SHADOW_MV, score=0.99
    )  # shadow
    res = _run(
        ids,
        user_id=ids["coordinator_a"],
        content="Operational status of this participant.",
        participant_id=ids["participant_a"],
        route_to=agent,
    )
    structured = [c for c in res["citations"] if c["source_type"] == "structured"]
    assert structured, f"{agent}: expected a champion structured citation"
    assert all(_CHAMPION_MV in c["locator"] for c in structured)
    assert all(_SHADOW_MV not in c["locator"] for c in structured), (
        f"{agent}: shadow model_version leaked into citations"
    )
