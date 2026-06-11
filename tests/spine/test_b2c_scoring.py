"""B2c scoring invariants: structural GBT shadow alongside the LSTM champion.

Invariants covered:
  (i)   Champion-only surfacing — arbiter of the storage open question.
  (ii)  Shadow inference fires leakage guards (no exemption for non-champion).
  Champion untouched: shadow does not perturb champion denorm cache.
  Tenant isolation: shadow participant_score rows respect sponsor RLS.
  Honesty: shadow model_version contains "structural", model_card_ref exists.

Tests do NOT use torch (shadow is sklearn-based).  No slow marker needed.
Requires: live Postgres, data/models/t2d/structural_v1.0_t2d.pkl.
"""

from __future__ import annotations

import asyncio
import os
import uuid
from pathlib import Path

import pytest

_SHADOW_ARTIFACT_PATH = Path("data/models/t2d/structural_v1.0_t2d.pkl")
_CHAMPION_MV = "sequence_v1.0:demo"
_SHADOW_MV = "structural_v1.0:t2d"


# ---------------------------------------------------------------------------
# 1. Artifact file exists
# ---------------------------------------------------------------------------


def test_artifact_file_exists_structural() -> None:
    """structural_v1.0_t2d.pkl must exist at the canonical path before shadow can run."""
    assert _SHADOW_ARTIFACT_PATH.exists(), (
        f"Structural shadow artifact not found at {_SHADOW_ARTIFACT_PATH}. "
        "Run: uv run python scripts/persist_structural_artifact.py"
    )


# ---------------------------------------------------------------------------
# 2. Invariant (i) — champion-only surfacing (arbiter of storage open question)
# ---------------------------------------------------------------------------


def test_invariant_i_champion_only_surfacing(migrated_db: dict[str, str]) -> None:
    """Inv (i): GET /cohort returns champion denorm data; direct DB shows both rows.

    Writes both champion and shadow participant_score rows manually (no score_trial
    needed here — tests the storage invariant directly at the DB + HTTP layers).
    """
    import asyncio

    from fastapi.testclient import TestClient

    from vigil.api.app import create_app
    from vigil.db.models import Participant
    from vigil.repositories import scoring as scoring_repo
    from vigil.repositories.session import sponsor_bootstrap_session
    from vigil.services.auth_service import login

    ids = migrated_db
    sponsor_id = uuid.UUID(ids["sponsor_a"])
    pid = uuid.UUID(ids["participant_a"])
    trial_id = uuid.UUID(ids["trial_a"])
    site_id = uuid.UUID(ids["site_a"])

    champion_score = 0.77
    shadow_score = 0.31  # deliberately different

    # Write champion participant_score row + set denorm on participant.
    with sponsor_bootstrap_session(str(sponsor_id)) as session:
        scoring_repo.upsert_score(
            session,
            participant_id=pid,
            sponsor_id=sponsor_id,
            trial_id=trial_id,
            site_id=site_id,
            risk_score=champion_score,
            risk_band="high",
            top_factors=[],
            reasons=[],
            model_version=_CHAMPION_MV,
            model_card_ref="data/models/t2d/model_card.md",
            synthetic=True,
        )
        from sqlalchemy import update

        session.execute(
            update(Participant)
            .where(Participant.id == pid)
            .values(risk_score=champion_score, risk_band="high")
        )

    # Write shadow participant_score row (no denorm update — shadow never writes it).
    with sponsor_bootstrap_session(str(sponsor_id)) as session:
        scoring_repo.upsert_score(
            session,
            participant_id=pid,
            sponsor_id=sponsor_id,
            trial_id=trial_id,
            site_id=site_id,
            risk_score=shadow_score,
            risk_band="low",
            top_factors=[],
            reasons=[],
            model_version=_SHADOW_MV,
            model_card_ref="data/models/t2d/model_card_structural.md",
            synthetic=True,
        )

    # Layer 1 guard: GET /cohort returns participant.risk_score (denorm), not shadow.
    async def _login(email: str, pw: str):  # type: ignore[no-untyped-def]
        return await login(email, pw)

    result = asyncio.get_event_loop().run_until_complete(
        _login(
            "coord.a@vigil.example",
            os.environ.get("VIGIL_SEED_PASSWORD", "vigil-dev-password"),
        )
    )
    token = result.token.access_token
    app = create_app()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/api/v1/cohort", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    items = resp.json()["items"]
    p_items = [r for r in items if r["participant_id"] == str(pid)]
    assert p_items, f"participant_a not in cohort response: {items}"
    surfaced_score = p_items[0]["risk_score"]
    assert abs(surfaced_score - champion_score) < 1e-6, (
        f"GET /cohort returned score {surfaced_score!r} — expected champion "
        f"{champion_score!r} from denorm cache, not shadow {shadow_score!r}. "
        "Champion-only surfacing layer 1 (denorm cache) BROKEN."
    )

    # Layer 2 guard (spec routing.md § (i)): GET /participants/{id}/risk returns the
    # champion model_version, never the shadow — even with an adversarial shadow row
    # present in participant_score. This is the champion-allowlist filter in action.
    risk_resp = client.get(
        f"/api/v1/participants/{pid}/risk",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert risk_resp.status_code == 200, (
        f"/risk returned {risk_resp.status_code}: {risk_resp.text}"
    )
    risk_body = risk_resp.json()
    assert risk_body["model_version"] == _CHAMPION_MV, (
        f"/risk returned model_version {risk_body['model_version']!r} — expected champion "
        f"{_CHAMPION_MV!r}, NOT shadow {_SHADOW_MV!r}. Layer 2 champion-only surfacing BROKEN."
    )
    assert risk_body["model_version"] != _SHADOW_MV, "shadow surfaced via /risk"
    assert abs(risk_body["risk_score"] - champion_score) < 1e-6, (
        f"/risk risk_score {risk_body['risk_score']!r} — expected champion {champion_score!r}"
    )

    # Layer 3: direct DB query shows BOTH rows (RLS does NOT hide shadow).
    with sponsor_bootstrap_session(str(sponsor_id)) as session:
        rows = scoring_repo.list_scores_for_participant(session, pid)
    model_versions = {r.model_version for r in rows}
    assert _CHAMPION_MV in model_versions, (
        f"Champion row missing from participant_score: {model_versions}"
    )
    assert _SHADOW_MV in model_versions, (
        f"Shadow row missing from participant_score: {model_versions}. "
        "RLS may be hiding the shadow row — that would be a DESIGN VIOLATION "
        "(model-role isolation must be enforced at app layer, not RLS)."
    )

    # VERDICT (verified under real Postgres): shared-table design HOLDS.
    #   Layer 1 (denorm cache) — GET /cohort surfaces champion only.
    #   Layer 2 (champion allowlist) — GET /participants/{id}/risk surfaces champion only;
    #            the shadow row, though present in participant_score, is filtered out by
    #            scoring_repo.get_surfaceable_score(champion_versions=...).
    #   Layer 3 (RLS) — both rows visible to the sponsor; suppression is app-layer, not RLS.


# ---------------------------------------------------------------------------
# 2b. Layer-2 fail-closed — shadow-only participant never surfaces via /risk
# ---------------------------------------------------------------------------


def test_layer2_risk_fails_closed_on_shadow_only(migrated_db: dict[str, str]) -> None:
    """A participant with ONLY a shadow participant_score row gets 404 from /risk.

    Proves the champion-allowlist filter fails closed: with no champion row, the
    clinical risk endpoint surfaces nothing rather than falling back to the shadow row.
    Also asserts the repository read excludes the shadow row directly.
    """
    from vigil.db.models import Participant
    from vigil.repositories import routing as routing_repo
    from vigil.repositories import scoring as scoring_repo
    from vigil.repositories.session import (
        platform_session,
        scoped_session,
        sponsor_bootstrap_session,
    )
    from vigil.services.scope_resolver import resolve_scope
    from vigil.repositories import users as user_repo
    from vigil.repositories.session import auth_lookup_session

    ids = migrated_db
    sponsor_id = uuid.UUID(ids["sponsor_a"])
    trial_id = uuid.UUID(ids["trial_a"])
    site_id = uuid.UUID(ids["site_a"])

    # Fresh, isolated participant — order-independent (the shared participant_a may have
    # a champion row written by the arbiter test under the session-scoped DB).
    pid = uuid.uuid4()
    with sponsor_bootstrap_session(str(sponsor_id)) as session:
        session.add(
            Participant(
                id=pid,
                sponsor_id=sponsor_id,
                trial_id=trial_id,
                site_id=site_id,
                coded_ref="b2c-layer2-failclosed-probe",
            )
        )
        session.flush()

    # Write ONLY a shadow row for this participant (no champion participant_score row).
    with sponsor_bootstrap_session(str(sponsor_id)) as session:
        scoring_repo.upsert_score(
            session,
            participant_id=pid,
            sponsor_id=sponsor_id,
            trial_id=trial_id,
            site_id=site_id,
            risk_score=0.91,
            risk_band="high",
            top_factors=[],
            reasons=[],
            model_version=_SHADOW_MV,
            model_card_ref="data/models/t2d/model_card_structural.md",
            synthetic=True,
        )

    # Repository read with the champion allowlist must NOT return the shadow row.
    with platform_session() as session:
        champion_versions = routing_repo.champion_model_versions(session)
    assert _SHADOW_MV not in champion_versions, "shadow leaked into champion allowlist"

    with auth_lookup_session() as session:
        user_a = user_repo.get_by_email(session, "coord.a@vigil.example")
    assert user_a is not None
    with auth_lookup_session() as session:
        scope_a = resolve_scope(session, user_a)

    with scoped_session(scope_a) as session:
        surfaced = scoring_repo.get_surfaceable_score(
            session, pid, champion_versions=champion_versions
        )
    assert surfaced is None, (
        f"get_surfaceable_score returned a row {surfaced!r} for a shadow-only participant — "
        "champion-allowlist filter is NOT fail-closed."
    )


# ---------------------------------------------------------------------------
# 3. Invariant (ii) — shadow leakage guard fires; structural features are clean
# ---------------------------------------------------------------------------


def test_invariant_ii_shadow_leakage_guard() -> None:
    """Inv (ii): assert_no_outcome_features passes for structural features, raises on forbidden."""
    import joblib
    import pandas as pd

    from ingestion.errors import LeakageError
    from models.leakage_check import assert_no_outcome_features
    from vigil.workers.tasks import StructuralScorer

    artifact = joblib.load(_SHADOW_ARTIFACT_PATH)
    scorer = StructuralScorer(artifact)

    participant_df = pd.DataFrame(
        [
            {
                "participant_id": str(uuid.uuid4()),
                "arm_type": "Placebo Comparator",
                "phase": "PHASE3",
                "n_sites": 3,
                "planned_duration_days": 365,
            }
        ]
    )

    # Structural feature names must be clean (no forbidden tokens).
    names = scorer.feature_names_for(participant_df)
    assert names, "StructuralScorer returned no feature names"
    assert_no_outcome_features(names)  # must NOT raise for clean structural features

    # Guard fires when a forbidden column is injected.
    with pytest.raises(LeakageError):
        assert_no_outcome_features(names + ["dropout_rate"])


# ---------------------------------------------------------------------------
# 4. Champion untouched after shadow runs
# ---------------------------------------------------------------------------


def test_champion_untouched_after_shadow(migrated_db: dict[str, str]) -> None:
    """Shadow scoring does not overwrite participant.risk_score set by champion."""
    from vigil.db.models import Participant
    from vigil.repositories.session import sponsor_bootstrap_session
    from vigil.workers.tasks import score_trial

    ids = migrated_db
    sponsor_id = ids["sponsor_a"]
    trial_id = ids["trial_a"]
    pid = uuid.UUID(ids["participant_a"])

    # Champion mock scorer — deterministic, torch-free.
    champion_sentinel = 0.888

    def _mock_champion(features):  # type: ignore[no-untyped-def]
        return [champion_sentinel] * len(features)

    asyncio.get_event_loop().run_until_complete(
        score_trial(
            {"job_try": 0, "_scorer_override": _mock_champion},
            trial_id=trial_id,
            sponsor_id=sponsor_id,
            model_version=None,
            regime="t2d",
        )
    )

    # Champion denorm must equal the mock champion score.
    with sponsor_bootstrap_session(sponsor_id) as session:
        p = session.get(Participant, pid)
    assert p is not None
    assert abs(p.risk_score - champion_sentinel) < 1e-6, (
        f"participant.risk_score={p.risk_score!r} — expected champion sentinel "
        f"{champion_sentinel!r}. Shadow may have overwritten the denorm cache."
    )


# ---------------------------------------------------------------------------
# 5. Tenant isolation: shadow rows respect sponsor RLS
# ---------------------------------------------------------------------------


def test_tenant_isolation_shadow_rows(migrated_db: dict[str, str]) -> None:
    """Shadow participant_score rows for Sponsor A are invisible to Sponsor B."""
    from vigil.repositories import scoring as scoring_repo
    from vigil.repositories.session import (
        scoped_session,
        sponsor_bootstrap_session,
    )
    from vigil.services.scope_resolver import resolve_scope
    from vigil.repositories import users as user_repo
    from vigil.repositories.session import auth_lookup_session

    ids = migrated_db
    sponsor_id = uuid.UUID(ids["sponsor_a"])
    pid = uuid.UUID(ids["participant_a"])
    trial_id = uuid.UUID(ids["trial_a"])
    site_id = uuid.UUID(ids["site_a"])

    # Write a shadow row for Sponsor A's participant.
    with sponsor_bootstrap_session(str(sponsor_id)) as session:
        scoring_repo.upsert_score(
            session,
            participant_id=pid,
            sponsor_id=sponsor_id,
            trial_id=trial_id,
            site_id=site_id,
            risk_score=0.42,
            risk_band="medium",
            top_factors=[],
            reasons=[],
            model_version=_SHADOW_MV,
            model_card_ref="data/models/t2d/model_card_structural.md",
            synthetic=True,
        )

    # Sponsor B's session must not see it.
    with auth_lookup_session() as session:
        user_b = user_repo.get_by_email(session, "coord.b@vigil.example")
    assert user_b is not None
    with auth_lookup_session() as session:
        scope_b = resolve_scope(session, user_b)

    with scoped_session(scope_b) as session:
        rows_b = scoring_repo.list_scores_for_participant(session, pid)

    shadow_b = [r for r in rows_b if r.model_version == _SHADOW_MV]
    assert shadow_b == [], (
        f"Sponsor A shadow row leaked to Sponsor B: {shadow_b}. "
        "Shadow participant_score rows must carry sponsor_id and obey RLS."
    )


# ---------------------------------------------------------------------------
# 6. Honesty: shadow model_version contains "structural", model_card_ref exists
# ---------------------------------------------------------------------------


def test_shadow_model_version_and_card(migrated_db: dict[str, str]) -> None:
    """Shadow routing_state row has model_version with 'structural' and a valid card path."""
    from vigil.repositories import routing as routing_repo
    from vigil.repositories.session import platform_session

    with platform_session() as session:
        row = routing_repo.get_shadow(session, regime="t2d")

    assert row is not None, "No shadow routing_state row for regime='t2d'"
    assert "structural" in row.model_version.lower(), (
        f"Shadow model_version '{row.model_version}' does not contain 'structural' — "
        "shadow must not be named 'sequence' (honesty invariant)"
    )
    assert "sequence" not in row.model_version.lower(), (
        f"Shadow model_version '{row.model_version}' contains 'sequence' — "
        "shadow is structural, not trajectory"
    )
    assert row.model_card_ref, "Shadow model_card_ref is empty"
    assert Path(row.model_card_ref).exists(), (
        f"Shadow model_card_ref path '{row.model_card_ref}' does not exist"
    )


# ---------------------------------------------------------------------------
# 7. StructuralScorer loaded from artifact (not LSTMScorer)
# ---------------------------------------------------------------------------


def test_structural_scorer_loaded_not_lstm(migrated_db: dict[str, str]) -> None:
    """_load_shadow_scorer returns StructuralScorer (real artifact) for regime='t2d'."""
    from vigil.workers.tasks import LSTMScorer, StructuralScorer, _load_shadow_scorer

    result = _load_shadow_scorer("t2d")
    assert result is not None, (
        "_load_shadow_scorer('t2d') returned None — shadow row or artifact missing"
    )
    scorer, mv = result
    assert isinstance(scorer, StructuralScorer), (
        f"Expected StructuralScorer, got {type(scorer).__name__}"
    )
    assert not isinstance(scorer, LSTMScorer), (
        "Shadow returned LSTMScorer — shadow must be structural, not sequence"
    )
    assert mv == _SHADOW_MV, f"Expected shadow mv={_SHADOW_MV!r}, got {mv!r}"
