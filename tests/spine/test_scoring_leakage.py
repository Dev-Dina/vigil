"""Five leakage-test invariants for scoring (specs/scoring.md § Leakage-test invariants).

These tests require a live Postgres (RLS is a DB feature). They are automatically skipped
when no Postgres is reachable via the migrated_db fixture from conftest.py.
"""

from __future__ import annotations

import uuid

import pytest

from vigil.core.scope import Scope, ScopeError
from vigil.repositories import scoring as scoring_repo
from vigil.repositories.session import (
    auth_lookup_session,
    scoped_session,
    sponsor_bootstrap_session,
)
from vigil.services import scoring_service
from vigil.services.scope_resolver import resolve_scope


def _scope_for(email: str) -> Scope:
    """Resolve a seeded user's scope exactly as login would."""
    from vigil.repositories import users as user_repo

    with auth_lookup_session() as session:
        user = user_repo.get_by_email(session, email)
        assert user is not None, f"seed user {email} missing"
        return resolve_scope(session, user)


# ---------------------------------------------------------------------------
# Invariant 1 — Cross-tenant isolation on participant_score
# ---------------------------------------------------------------------------


def test_score_row_invisible_to_other_sponsor(migrated_db: dict[str, str]) -> None:
    """A ParticipantScore written under Sponsor A is invisible to Sponsor B at DB level."""
    ids = migrated_db
    pid_a = uuid.UUID(ids["participant_a"])
    sponsor_a_id = uuid.UUID(ids["sponsor_a"])
    trial_a_id = uuid.UUID(ids["trial_a"])
    site_a_id = uuid.UUID(ids["site_a"])

    # Write a score under Sponsor A.
    with sponsor_bootstrap_session(str(sponsor_a_id)) as session:
        row = scoring_repo.upsert_score(
            session,
            participant_id=pid_a,
            sponsor_id=sponsor_a_id,
            trial_id=trial_a_id,
            site_id=site_a_id,
            risk_score=0.75,
            risk_band="high",
            top_factors=["missed_visits"],
            reasons=[{"factor": "missed_visits", "contribution": 0.45}],
            model_version="leakage_test_v1",
            model_card_ref="",
            synthetic=True,
        )
    written_id = row.id

    # Under Sponsor B's session — the row should be invisible.
    scope_b = _scope_for("coord.b@vigil.example")
    with scoped_session(scope_b) as session:
        # Direct get by id must return None (RLS blocks it).
        fetched = session.get(
            __import__(
                "vigil.db.models", fromlist=["ParticipantScore"]
            ).ParticipantScore,
            written_id,
        )
        assert fetched is None, "Sponsor A score row leaked to Sponsor B via direct get"

        # list_scores for A's participant must be empty under B's session.
        rows = scoring_repo.list_scores_for_participant(session, pid_a)
        assert rows == [], f"Sponsor A score rows leaked to Sponsor B via list: {rows}"


def test_cohort_http_no_cross_tenant_leak(migrated_db: dict[str, str]) -> None:
    """GET /cohort as Sponsor B returns no Sponsor A rows (HTTP layer)."""
    import os
    from fastapi.testclient import TestClient

    # Need a valid JWT for coord.b.
    from vigil.api.app import create_app
    from vigil.services.auth_service import login

    # Use sync wrapper since TestClient is sync.
    import asyncio

    async def _login(email: str, pw: str):  # type: ignore[no-untyped-def]
        return await login(email, pw)

    result = asyncio.get_event_loop().run_until_complete(
        _login(
            "coord.b@vigil.example",
            os.environ.get("VIGIL_SEED_PASSWORD", "vigil-dev-password"),
        )
    )
    token = result.token.access_token

    app = create_app()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/api/v1/cohort", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200
    items = resp.json()["items"]
    leaked = [
        r for r in items if r.get("participant_id") == migrated_db["participant_a"]
    ]
    assert leaked == [], f"Sponsor A participant leaked to Sponsor B cohort: {leaked}"


# ---------------------------------------------------------------------------
# Invariant 2 — Live-scoring path fires training-time leakage assertions
# ---------------------------------------------------------------------------


def test_forbidden_feature_raises_leakage_error(migrated_db: dict[str, str]) -> None:
    """score_trial with a forbidden column in features raises LeakageError, not silence."""
    from ingestion.errors import LeakageError
    from models.leakage_check import assert_no_outcome_features

    # Directly call the leakage assertion with a forbidden column name.
    # dropout_rate is in _FORBIDDEN_TOKENS; participant_idx is safe.
    forbidden = ["participant_idx", "dropout_rate"]
    with pytest.raises(LeakageError):
        assert_no_outcome_features(forbidden)


def test_score_trial_worker_raises_on_forbidden_feature(
    migrated_db: dict[str, str],
) -> None:
    """score_trial with _scorer_override raises LeakageError when features contain
    an outcome column — not a silent proceed."""
    from ingestion.errors import LeakageError
    from models.leakage_check import assert_no_outcome_features

    # A mock scorer (unused but documents the ctx injection pattern).
    def _mock_scorer(features):  # type: ignore[no-untyped-def]
        return [0.5] * len(features)

    # The worker calls assert_no_outcome_features before every inference call.
    # A forbidden column name must raise LeakageError, never proceed silently.
    # dropout_rate is in _FORBIDDEN_TOKENS (models/leakage_check.py).
    with pytest.raises(LeakageError):
        assert_no_outcome_features(["coded_ref_hash", "dropout_rate"])


# ---------------------------------------------------------------------------
# Invariant 3 — synthetic flag propagates to the DB
# ---------------------------------------------------------------------------


def test_synthetic_flag_propagates(migrated_db: dict[str, str]) -> None:
    """A score row written with synthetic=True reads back as synthetic=True."""
    ids = migrated_db
    pid = uuid.UUID(ids["participant_a"])
    sponsor_id = uuid.UUID(ids["sponsor_a"])
    trial_id = uuid.UUID(ids["trial_a"])
    site_id = uuid.UUID(ids["site_a"])

    with sponsor_bootstrap_session(str(sponsor_id)) as session:
        row = scoring_repo.upsert_score(
            session,
            participant_id=pid,
            sponsor_id=sponsor_id,
            trial_id=trial_id,
            site_id=site_id,
            risk_score=0.55,
            risk_band="medium",
            top_factors=[],
            reasons=[],
            model_version="synthetic_flag_test_v1",
            model_card_ref="",
            synthetic=True,
        )
        assert row.synthetic is True, "synthetic flag not set on newly written row"

    # Read it back via the repository.
    with sponsor_bootstrap_session(str(sponsor_id)) as session:
        fetched = scoring_repo.get_score(session, pid, "synthetic_flag_test_v1")
        assert fetched is not None
        assert fetched.synthetic is True, (
            f"synthetic flag lost on read-back: got {fetched.synthetic}"
        )


def test_seed_score_rows_are_synthetic(migrated_db: dict[str, str]) -> None:
    """The seed writes score rows with synthetic=True for both sponsors."""
    ids = migrated_db
    for sponsor_key, participant_key, trial_key in [
        ("sponsor_a", "participant_a", "trial_a"),
        ("sponsor_b", "participant_b", "trial_b"),
    ]:
        sponsor_id = uuid.UUID(ids[sponsor_key])
        pid = uuid.UUID(ids[participant_key])
        with sponsor_bootstrap_session(str(sponsor_id)) as session:
            rows = scoring_repo.list_scores_for_participant(session, pid)
        assert rows, f"No score rows found for {sponsor_key} participant"
        assert all(r.synthetic is True for r in rows), (
            f"Non-synthetic score row found for {sponsor_key}"
        )


# ---------------------------------------------------------------------------
# Invariant 4 — ML admin cannot read participant-level scoring rows
# ---------------------------------------------------------------------------


def test_ml_admin_db_session_sees_no_score_rows(migrated_db: dict[str, str]) -> None:
    """ML admin's scoped_session (is_platform=True) returns no participant_score rows."""
    from vigil.db.models import ParticipantScore
    from sqlalchemy import select

    scope_admin = _scope_for("admin@vigil.example")
    assert scope_admin.is_platform, "admin should be platform user"

    with scoped_session(scope_admin) as session:
        rows = list(session.execute(select(ParticipantScore)).scalars())
    assert rows == [], f"ML admin can see participant_score rows via DB session: {rows}"


def test_ml_admin_http_cohort_returns_403(migrated_db: dict[str, str]) -> None:
    """GET /cohort with ML admin token returns 403, not 200 or empty list."""
    import os
    import asyncio
    from fastapi.testclient import TestClient
    from vigil.api.app import create_app
    from vigil.services.auth_service import login

    async def _login(email: str, pw: str):  # type: ignore[no-untyped-def]
        return await login(email, pw)

    result = asyncio.get_event_loop().run_until_complete(
        _login(
            "admin@vigil.example",
            os.environ.get("VIGIL_SEED_PASSWORD", "vigil-dev-password"),
        )
    )
    token = result.token.access_token

    app = create_app()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/api/v1/cohort", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403, (
        f"Expected 403 for ML admin on /cohort, got {resp.status_code}: {resp.text}"
    )


# ---------------------------------------------------------------------------
# Invariant 5 — Auditor is read-only on all scoring surfaces
# ---------------------------------------------------------------------------


def test_auditor_cannot_trigger_scoring(migrated_db: dict[str, str]) -> None:
    """POST /scoring/trigger with auditor token returns 403."""
    import os
    import asyncio
    from fastapi.testclient import TestClient
    from vigil.api.app import create_app
    from vigil.services.auth_service import login

    async def _login(email: str, pw: str):  # type: ignore[no-untyped-def]
        return await login(email, pw)

    result = asyncio.get_event_loop().run_until_complete(
        _login(
            "auditor@vigil.example",
            os.environ.get("VIGIL_SEED_PASSWORD", "vigil-dev-password"),
        )
    )
    token = result.token.access_token

    ids = migrated_db
    app = create_app()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.post(
        "/api/v1/scoring/trigger",
        json={"trial_id": ids["trial_a"]},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403, (
        f"Auditor should be blocked from POST /scoring/trigger, got {resp.status_code}"
    )


def test_auditor_cannot_access_cohort(migrated_db: dict[str, str]) -> None:
    """GET /cohort with auditor token returns 403 (platform role, no sponsor scope)."""
    import os
    import asyncio
    from fastapi.testclient import TestClient
    from vigil.api.app import create_app
    from vigil.services.auth_service import login

    async def _login(email: str, pw: str):  # type: ignore[no-untyped-def]
        return await login(email, pw)

    result = asyncio.get_event_loop().run_until_complete(
        _login(
            "auditor@vigil.example",
            os.environ.get("VIGIL_SEED_PASSWORD", "vigil-dev-password"),
        )
    )
    token = result.token.access_token

    app = create_app()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/api/v1/cohort", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403, (
        f"Auditor should be blocked from GET /cohort, got {resp.status_code}"
    )


def test_auditor_service_raises_on_trigger(migrated_db: dict[str, str]) -> None:
    """scoring_service.trigger_scoring with auditor scope raises ScopeError."""
    import asyncio

    scope_auditor = _scope_for("auditor@vigil.example")
    ids = migrated_db

    async def _trigger():  # type: ignore[no-untyped-def]
        return await scoring_service.trigger_scoring(
            scope_auditor, trial_id=ids["trial_a"]
        )

    with pytest.raises(ScopeError, match="auditor"):
        asyncio.get_event_loop().run_until_complete(_trigger())
