"""Gate 9.3 — recommended protocol actions (operational coordinator next-steps).

Asserts: the mapping is TRANSPARENT + DETERMINISTIC (a static band+factor map, not an LLM); the
actions are OPERATIONAL-not-clinical (every surfaced action is in the approved catalog, no
medical/diagnosis/treatment text); they map to the existing InterventionKind taxonomy; and the
surface is SCOPE-BOUND (inherits SEC-1 — an out-of-scope participant's actions never surface) and
synthetic-labelled.

Real Postgres via migrated_db.
"""

from __future__ import annotations

import os
import re
import uuid

from fastapi.testclient import TestClient

from vigil.api.app import create_app
from vigil.domain import InterventionKind
from vigil.services import recommended_actions as ra

_CHAMPION_MV = "sequence_v1.1:demo"
_CARD = "data/models/t2d/model_card.md"

# Tokens that must NEVER appear in an operational action (this layer gives no clinical advice).
_MEDICAL = re.compile(
    r"\b(diagnos\w*|prescrib\w*|medication|drug|dose|dosage|treatment|therapy|cancer|"
    r"insulin|mg|prognos\w*|cure)\b",
    re.IGNORECASE,
)
_KINDS = {k.value for k in InterventionKind}


# ---------------------------------------------------------------------------
# 1. The mapping itself: transparent, deterministic, operational-only
# ---------------------------------------------------------------------------


def test_mapping_is_deterministic_and_operational() -> None:
    a1 = ra.recommend("high", ["consecutive_missed", "visit_index"])
    a2 = ra.recommend("high", ["consecutive_missed", "visit_index"])
    assert [(x.action, x.intervention_kind, x.factor) for x in a1] == [
        (x.action, x.intervention_kind, x.factor) for x in a2
    ], "mapping must be deterministic (static, not an LLM)"

    assert a1, "a high-risk participant must get at least the baseline action"
    for x in a1:
        # Operational-only: every action string is from the closed approved catalog.
        assert x.action in ra.APPROVED_ACTION_TEXTS, (
            f"action not in approved set: {x.action!r}"
        )
        # No clinical/medical content anywhere in the action text.
        assert not _MEDICAL.search(x.action), f"medical content in action: {x.action!r}"
        # Maps to the existing audited InterventionKind taxonomy.
        assert x.intervention_kind in _KINDS, (
            f"unknown intervention_kind: {x.intervention_kind!r}"
        )


def test_missed_visit_factor_maps_to_contact_call() -> None:
    actions = ra.recommend("high", ["consecutive_missed"])
    contact = next(
        (a for a in actions if a.action == "Contact participant about missed visits"),
        None,
    )
    assert contact is not None, (
        "a missed-visit driver must suggest contacting the participant"
    )
    assert contact.intervention_kind == InterventionKind.CALL.value
    assert contact.factor == "consecutive_missed"


def test_non_high_band_gets_no_actions() -> None:
    assert ra.recommend("medium", ["consecutive_missed"]) == []
    assert ra.recommend("low", ["consecutive_missed"]) == []


def test_high_with_no_recognized_factors_gets_baseline() -> None:
    actions = ra.recommend("high", [])
    assert [a.action for a in actions] == ["Flag for site/clinic follow-up"]
    assert actions[0].intervention_kind == InterventionKind.NOTE.value


# ---------------------------------------------------------------------------
# 2. End-to-end on the scope-bound risk detail
# ---------------------------------------------------------------------------


def _client() -> TestClient:
    return TestClient(create_app(), raise_server_exceptions=False)


def _token(client: TestClient, email: str) -> str:
    pw = os.environ.get("VIGIL_SEED_PASSWORD", "vigil-dev-password")
    r = client.post("/api/v1/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _h(tok: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {tok}"}


def _high_participant_other_site(ids: dict[str, str], *, feature: str) -> str:
    """A high champion-scored participant under a NEW trial+site of Sponsor A, with a champion
    reason on ``feature`` (out of coord.a's site scope; visible to sponsor-oversight)."""
    from vigil.db.models import Participant, Site, Trial
    from vigil.repositories import scoring as scoring_repo
    from vigil.repositories.session import sponsor_bootstrap_session

    sponsor_a = uuid.UUID(ids["sponsor_a"])
    with sponsor_bootstrap_session(ids["sponsor_a"]) as session:
        t = Trial(sponsor_id=sponsor_a, name="actions-probe-trial")
        session.add(t)
        session.flush()
        s = Site(sponsor_id=sponsor_a, trial_id=t.id, name="actions-probe-site")
        session.add(s)
        session.flush()
        p = Participant(
            sponsor_id=sponsor_a,
            trial_id=t.id,
            site_id=s.id,
            coded_ref="PT-ACTIONS",
            status="active",
            risk_score=0.9,
            risk_band="high",
        )
        session.add(p)
        session.flush()
        scoring_repo.append_score(
            session,
            participant_id=p.id,
            sponsor_id=sponsor_a,
            trial_id=t.id,
            site_id=s.id,
            risk_score=0.9,
            risk_band="high",
            top_factors=[feature],
            reasons=[
                {"feature": feature, "contribution": 0.2, "method": "lstm_occlusion"}
            ],
            model_version=_CHAMPION_MV,
            model_card_ref=_CARD,
            synthetic=True,
        )
        return str(p.id)


def test_risk_detail_surfaces_operational_actions(migrated_db: dict[str, str]) -> None:
    ids = migrated_db
    pid = _high_participant_other_site(ids, feature="consecutive_missed")
    client = _client()
    tok = _token(client, "oversight.a@vigil.example")  # sponsor-wide, in scope
    r = client.get(f"/api/v1/participants/{pid}/risk", headers=_h(tok))
    assert r.status_code == 200, r.text
    body = r.json()

    actions = body["recommended_actions"]
    assert actions, "high-risk participant must surface suggested actions"
    texts = [a["action"] for a in actions]
    assert "Contact participant about missed visits" in texts
    # Operational-only: every surfaced action is approved + carries a valid InterventionKind.
    for a in actions:
        assert a["action"] in ra.APPROVED_ACTION_TEXTS
        assert not _MEDICAL.search(a["action"])
        assert a["intervention_kind"] in _KINDS
    # Synthetic label governs the surface.
    assert body["synthetic"] is True


def test_actions_scope_bound_out_of_scope_404(migrated_db: dict[str, str]) -> None:
    """SEC-1 inherited: a site coordinator cannot see another site's participant's actions."""
    ids = migrated_db
    pid = _high_participant_other_site(ids, feature="consecutive_missed")
    client = _client()
    tok = _token(client, "coord.a@vigil.example")  # site_a/trial_a only
    r = client.get(f"/api/v1/participants/{pid}/risk", headers=_h(tok))
    assert r.status_code == 404, (
        f"cross-site risk/actions reachable: {r.status_code} (expected 404)"
    )
