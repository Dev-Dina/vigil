"""Gate OPS-ASSIST — the scoped ``cohort_at_risk`` triage tool: scope isolation + clinical refusal.

``cohort_at_risk`` reuses the existing scoped at-risk read (RLS axis-1 cross-tenant +
``scope.permits`` axis-2 cross-site + champion-only facts) under the caller's RE-RESOLVED scope. This
proves a coordinator sees ONLY their scoped high-risk participants (cross-tenant impossible by
construction), that "needs intervention" reflects the real interventions read, that the clinical
guardrail STILL refuses a treatment ask (the tool returns operational status, never clinical advice),
and that a triage question injects the scoped cohort as grounded context. Real Postgres via migrated_db.
"""

from __future__ import annotations

import uuid

from vigil.agents import tools
from vigil.agents.guardrails import guard_inbound
from vigil.db.models import Participant
from vigil.repositories import scoring as scoring_repo
from vigil.repositories import tenancy as tenancy_repo
from vigil.repositories.session import sponsor_bootstrap_session

_CHAMPION_MV = "sequence_v1.1:demo"
_CARD = "data/models/t2d/model_card.md"


def _make_high_participant(
    ids: dict[str, str],
    *,
    sponsor_key: str,
    trial_key: str,
    site_key: str,
    coded_ref: str,
) -> str:
    """A high-risk participant (denorm risk_band='high' + a champion score) in the given scope."""
    sponsor_id = uuid.UUID(ids[sponsor_key])
    trial_id = uuid.UUID(ids[trial_key])
    site_id = uuid.UUID(ids[site_key])
    with sponsor_bootstrap_session(ids[sponsor_key]) as session:
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


def test_cohort_at_risk_scoped_to_caller(migrated_db: dict[str, str]) -> None:
    ids = migrated_db
    pa = _make_high_participant(
        ids,
        sponsor_key="sponsor_a",
        trial_key="trial_a",
        site_key="site_a",
        coded_ref="PT-OPS-A",
    )
    pb = _make_high_participant(
        ids,
        sponsor_key="sponsor_b",
        trial_key="trial_b",
        site_key="site_b",
        coded_ref="PT-OPS-B",
    )

    with tools.agent_tool_context(ids["coordinator_a"]) as ctx:
        rows_a = tools.cohort_at_risk(ctx)
    ids_a = {r.participant_id for r in rows_a}
    assert pa in ids_a, "coordinator A could not see their OWN high-risk participant"
    assert pb not in ids_a, (
        "CROSS-TENANT LEAK: coordinator A saw Sponsor B's at-risk participant"
    )
    assert all(r.risk_band == "high" for r in rows_a)
    assert all(r.coded_ref is not None for r in rows_a), (
        "identity role should surface coded refs"
    )

    with tools.agent_tool_context(ids["coordinator_b"]) as ctx:
        rows_b = tools.cohort_at_risk(ctx)
    ids_b = {r.participant_id for r in rows_b}
    assert pb in ids_b, "coordinator B could not see their OWN high-risk participant"
    assert pa not in ids_b, (
        "CROSS-TENANT LEAK: coordinator B saw Sponsor A's at-risk participant"
    )


def test_cohort_at_risk_needs_intervention_flag(migrated_db: dict[str, str]) -> None:
    ids = migrated_db
    pa = _make_high_participant(
        ids,
        sponsor_key="sponsor_a",
        trial_key="trial_a",
        site_key="site_a",
        coded_ref="PT-OPS-NEEDS",
    )
    with tools.agent_tool_context(ids["coordinator_a"]) as ctx:
        row = next(r for r in tools.cohort_at_risk(ctx) if r.participant_id == pa)
    assert row.needs_intervention is True and row.intervention_logged is False

    with sponsor_bootstrap_session(ids["sponsor_a"]) as session:
        tenancy_repo.add_intervention(
            session,
            sponsor_id=uuid.UUID(ids["sponsor_a"]),
            participant_id=uuid.UUID(pa),
            kind="call",
            note="outreach",
            actor_user_id=uuid.UUID(ids["coordinator_a"]),
        )
    with tools.agent_tool_context(ids["coordinator_a"]) as ctx:
        row2 = next(r for r in tools.cohort_at_risk(ctx) if r.participant_id == pa)
    assert row2.intervention_logged is True and row2.needs_intervention is False


def test_treatment_question_still_refuses_operational_allowed() -> None:
    # A clinical/treatment ask is BLOCKED by the existing guardrail — the new tool changes nothing.
    for q in (
        "What is the best treatment for this participant?",
        "What medication should participant A-0001 take?",
    ):
        g = guard_inbound(q)
        assert g.result.decision == "blocked" and g.result.category == "clinical", q
    # An operational triage ask is ALLOWED (answered by cohort_at_risk, never the guardrail).
    ok = guard_inbound("Who in my cohort needs follow-up?")
    assert ok.result.decision == "allowed"


def test_cohort_question_injects_scoped_context(migrated_db: dict[str, str]) -> None:
    from vigil.agents import retention
    from vigil.agents.llm import LLMResponse

    class _FixedLLM:
        def complete(
            self, messages, *, temperature: float = 0.0, model=None
        ) -> LLMResponse:  # type: ignore[no-untyped-def]
            return LLMResponse(
                content="Here are the at-risk participants.", model="test/fixed"
            )

    ids = migrated_db
    _make_high_participant(
        ids,
        sponsor_key="sponsor_a",
        trial_key="trial_a",
        site_key="site_a",
        coded_ref="PT-OPS-INJ",
    )
    with tools.agent_tool_context(ids["coordinator_a"]) as ctx:
        ans = retention.answer(ctx, _FixedLLM(), "Who needs intervention?")
    assert ans.status == "ok"
    assert any(c.get("source_id") == "cohort:at_risk" for c in ans.citations), (
        "cohort_at_risk context was not injected for a triage question"
    )
