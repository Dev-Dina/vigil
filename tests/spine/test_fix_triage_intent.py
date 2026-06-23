"""Gate FIX-TRIAGE-INTENT — broadened cohort-triage intent match + honest empty-cohort message.

The scoped ``cohort_at_risk`` tool is UNCHANGED; only the deterministic intent GATE that decides when
to call it is widened (natural phrasings like "which of my participants are at risk" now route), and
the empty-result message for that path is made honest ("none at risk") instead of the generic refusal.
Single-participant + methodology questions still do NOT route to the cohort list, and the clinical
guardrail still fires unchanged.
"""

from __future__ import annotations

import uuid

import pytest

from vigil.agents import retention, tools
from vigil.agents.agent_base import (
    _COHORT_NONE_AT_RISK,
    _REFUSAL,
    _is_cohort_triage_question,
    grounded_answer,
)
from vigil.agents.guardrails import guard_inbound
from vigil.agents.llm import LLMResponse
from vigil.db.models import Participant
from vigil.repositories import scoring as scoring_repo
from vigil.repositories.session import sponsor_bootstrap_session

_CHAMPION_MV = "sequence_v1.1:demo"
_CARD = "data/models/t2d/model_card.md"


class _FixedLLM:
    def complete(self, messages, *, temperature: float = 0.0, model=None):  # type: ignore[no-untyped-def]
        return LLMResponse(content="(grounded answer)", model="test/fixed")


def _make_high(ids: dict[str, str], *, coded_ref: str) -> str:
    """A high-risk participant (denorm band='high' + a champion score) in coordinator A's scope."""
    sponsor_id = uuid.UUID(ids["sponsor_a"])
    trial_id = uuid.UUID(ids["trial_a"])
    site_id = uuid.UUID(ids["site_a"])
    with sponsor_bootstrap_session(ids["sponsor_a"]) as session:
        p = Participant(
            sponsor_id=sponsor_id,
            trial_id=trial_id,
            site_id=site_id,
            coded_ref=coded_ref,
            status="active",
            risk_score=0.82,
            risk_band="high",
        )
        session.add(p)
        session.flush()
        pid = p.id
        scoring_repo.append_score(
            session,
            participant_id=pid,
            sponsor_id=sponsor_id,
            trial_id=trial_id,
            site_id=site_id,
            risk_score=0.82,
            risk_band="high",
            top_factors=["consecutive_missed"],
            reasons=[],
            model_version=_CHAMPION_MV,
            model_card_ref=_CARD,
            synthetic=True,
        )
    return str(pid)


# ---------------------------------------------------------------------------
# 1. the intent gate, BOTH directions (pure — no DB)
# ---------------------------------------------------------------------------

_ROUTES = [
    "which of my participants are at risk",  # the confirmed-live MISS — now routes
    "who's at risk of dropping out",
    "which participants need follow-up",
    "who might drop out",
    "at-risk participants in my cohort",
    "who needs attention",
    "which of my participants need follow-up",
    "Who needs intervention?",  # the original phrasing still routes (no regression)
]

_DOES_NOT_ROUTE = [
    "why is A-0005 at risk?",  # single-participant explanation → participant path
    "how does the model assess risk",  # methodology
    "what is the dropout rate methodology",  # methodology mentioning dropout
    "who wrote the model card",  # unrelated
]


@pytest.mark.parametrize("q", _ROUTES)
def test_triage_phrasings_route(q: str) -> None:
    assert _is_cohort_triage_question(q) is True, (
        f"should route to cohort_at_risk: {q!r}"
    )


@pytest.mark.parametrize("q", _DOES_NOT_ROUTE)
def test_non_triage_phrasings_do_not_route(q: str) -> None:
    assert _is_cohort_triage_question(q) is False, (
        f"must NOT route to cohort_at_risk: {q!r}"
    )


# ---------------------------------------------------------------------------
# 2. a broadened phrasing routes END-TO-END to cohort_at_risk
# ---------------------------------------------------------------------------


def test_broadened_phrasing_injects_cohort(migrated_db: dict[str, str]) -> None:
    ids = migrated_db
    _make_high(ids, coded_ref="PT-TRIAGE-BROAD")
    with tools.agent_tool_context(ids["coordinator_a"]) as ctx:
        ans = retention.answer(
            ctx, _FixedLLM(), "which of my participants are at risk?"
        )
    assert ans.status == "ok"
    assert any(c.get("source_id") == "cohort:at_risk" for c in ans.citations), (
        "broadened triage phrasing did not route to cohort_at_risk"
    )


# ---------------------------------------------------------------------------
# 3. a single-participant question takes the participant path, NOT the cohort list
# ---------------------------------------------------------------------------


def test_single_participant_not_cohort(migrated_db: dict[str, str]) -> None:
    ids = migrated_db
    _make_high(ids, coded_ref="PT-SINGLE-Q")
    with tools.agent_tool_context(ids["coordinator_a"]) as ctx:
        ans = retention.answer(ctx, _FixedLLM(), "why is PT-SINGLE-Q at risk?")
    source_ids = {c.get("source_id") for c in ans.citations}
    assert "cohort:at_risk" not in source_ids, (
        "single-participant question wrongly routed to the cohort list"
    )
    assert "participant:PT-SINGLE-Q" in source_ids, "participant path was not taken"


# ---------------------------------------------------------------------------
# 4. empty-scope cohort triage → honest "none at risk", NOT the generic refusal
# ---------------------------------------------------------------------------


def test_empty_cohort_triage_honest_message(
    migrated_db: dict[str, str], monkeypatch
) -> None:
    ids = migrated_db
    # Force the scoped read to find NOBODY + no doc grounding, deterministically. The tool's scope
    # reads are unchanged — we only assert the MESSAGE the empty cohort-triage path produces.
    monkeypatch.setattr("vigil.agents.agent_base.cohort_at_risk", lambda ctx: [])
    monkeypatch.setattr(
        "vigil.agents.agent_base.search_documents", lambda ctx, q, k=4: []
    )
    with tools.agent_tool_context(ids["coordinator_a"]) as ctx:
        ans = grounded_answer(
            ctx,
            _FixedLLM(),
            "which of my participants are at risk?",
            system_prompt="(test)",
        )
    assert ans.content == _COHORT_NONE_AT_RISK
    assert ans.content != _REFUSAL, (
        "empty cohort triage must not give the generic refusal"
    )
    assert ans.status == "ok"
    assert any(c.get("source_id") == "cohort:at_risk" for c in ans.citations)


# ---------------------------------------------------------------------------
# 5. the clinical guardrail still fires unchanged (the gate touches no guardrail)
# ---------------------------------------------------------------------------


def test_clinical_question_still_refuses() -> None:
    g = guard_inbound("What treatment should participant A-0001 take?")
    assert g.result.decision == "blocked" and g.result.category == "clinical"
