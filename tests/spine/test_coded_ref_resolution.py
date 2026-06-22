"""Gate CODED-REF — the assistant resolves a participant by EITHER coded_ref or UUID, scoped.

Proves:
- a coded_ref ("A-9001") and the internal UUID resolve to the SAME in-scope participant via
  ``participant_risk_facts`` (the coded_ref is surfaced in RiskFacts for an identity/site role);
- a coded_ref OUTSIDE the caller's scope resolves to NOTHING — no cross-tenant leak, no error that
  leaks existence (the SAME scoped read as every other tool; RLS hides the other sponsor's row);
- ``agent_base`` routes a free-text "why is <coded_ref> flagged?" to the participant path and refers
  to the participant by coded_ref (not the UUID) in its citation;
- the clinical AND identity guardrails STILL refuse for a coded ref — making the ref RESOLVABLE
  (Gate CODED-REF) does NOT relax either guardrail.

Real Postgres via migrated_db (RLS is a DB feature). Mirrors tests/spine/test_ops_assist_cohort.py.
"""

from __future__ import annotations

import uuid

from vigil.agents import tools
from vigil.agents.guardrails import guard_inbound
from vigil.db.models import Participant
from vigil.repositories import scoring as scoring_repo
from vigil.repositories.session import sponsor_bootstrap_session

_CHAMPION_MV = "sequence_v1.1:demo"
_CARD = "data/models/t2d/model_card.md"


def _make_scored(
    ids: dict[str, str],
    *,
    sponsor_key: str,
    trial_key: str,
    site_key: str,
    coded_ref: str,
    score: float = 0.82,
) -> str:
    """A champion-scored participant with a CHOSEN coded_ref in the given scope. Returns the UUID."""
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
            risk_score=score,
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
            risk_score=score,
            risk_band="high",
            top_factors=["consecutive_missed"],
            reasons=[],
            model_version=_CHAMPION_MV,
            model_card_ref=_CARD,
            synthetic=True,
        )
    return str(pid)


def test_coded_ref_and_uuid_resolve_to_same_participant(
    migrated_db: dict[str, str],
) -> None:
    ids = migrated_db
    pid = _make_scored(
        ids,
        sponsor_key="sponsor_a",
        trial_key="trial_a",
        site_key="site_a",
        coded_ref="A-9001",
    )
    with tools.agent_tool_context(ids["coordinator_a"]) as ctx:
        by_code = tools.participant_risk_facts(ctx, "A-9001")
        by_uuid = tools.participant_risk_facts(ctx, pid)

    assert by_code is not None, "coded_ref did not resolve to the in-scope participant"
    assert by_uuid is not None
    assert by_code.participant_id == pid == by_uuid.participant_id, (
        "coded_ref and UUID must resolve to the SAME participant"
    )
    # Identity (site) role surfaces the human-readable ref; the UUID stays the internal locator.
    assert by_code.coded_ref == "A-9001"
    assert by_uuid.coded_ref == "A-9001"


def test_coded_ref_outside_scope_resolves_to_nothing(
    migrated_db: dict[str, str],
) -> None:
    ids = migrated_db
    # A Sponsor-B participant with its own coded_ref. Coordinator A must NOT resolve it (axis-1
    # cross-tenant): RLS hides B's row from A's scoped session → the coded_ref lookup returns [].
    _make_scored(
        ids,
        sponsor_key="sponsor_b",
        trial_key="trial_b",
        site_key="site_b",
        coded_ref="B-9001",
    )
    with tools.agent_tool_context(ids["coordinator_a"]) as ctx:
        leaked = tools.participant_risk_facts(ctx, "B-9001")
    assert leaked is None, (
        "CROSS-TENANT LEAK: coordinator A resolved Sponsor B's coded_ref — coded_ref resolution "
        "must use the SAME scoped read, never a cross-tenant lookup"
    )


def test_assistant_routes_free_text_coded_ref_and_refers_by_code(
    migrated_db: dict[str, str],
) -> None:
    from vigil.agents import retention
    from vigil.agents.llm import LLMResponse

    class _FixedLLM:
        def complete(
            self, messages, *, temperature: float = 0.0, model=None
        ) -> LLMResponse:  # type: ignore[no-untyped-def]
            return LLMResponse(
                content="A-9001 is high risk, driven by consecutive missed visits.",
                model="test/fixed",
            )

    ids = migrated_db
    pid = _make_scored(
        ids,
        sponsor_key="sponsor_a",
        trial_key="trial_a",
        site_key="site_a",
        coded_ref="A-9001",
    )
    # No explicit participant_id — the ref is pulled from the free-text message and resolved.
    with tools.agent_tool_context(ids["coordinator_a"]) as ctx:
        ans = retention.answer(ctx, _FixedLLM(), "Why is A-9001 flagged?")

    assert ans.status == "ok"
    assert any(c.get("source_id") == "participant:A-9001" for c in ans.citations), (
        f"free-text coded_ref was not resolved/grounded; citations={ans.citations}"
    )
    # The citation refers to the participant by coded_ref, NEVER the raw UUID.
    assert all(c.get("source_id") != f"participant:{pid}" for c in ans.citations)


def test_clinical_and_identity_guardrails_still_refuse_for_a_coded_ref() -> None:
    # Resolving a coded ref does NOT relax the guardrails (pure boundary; no LLM/DB).
    clinical = guard_inbound("What is the best treatment for A-9001?")
    assert clinical.result.decision == "blocked"
    assert clinical.result.category == "clinical"

    identity = guard_inbound("What is A-9001's name?")
    assert identity.result.decision == "blocked"
    assert identity.result.category == "identity"

    # The ALLOWED operational question about the same coded ref is not blocked.
    ok = guard_inbound("Why is A-9001 flagged high risk?")
    assert ok.result.decision == "allowed"
