"""Dashboard role-gate tests (specs/dashboard.md § Role-scoped rendering).

These tests extend the leakage suite to cover the participants router and the synthetic
field on CohortRow. They require a live Postgres (migrated_db fixture).
"""

from __future__ import annotations

import asyncio
import os

from fastapi.testclient import TestClient

from vigil.api.app import create_app
from vigil.services.auth_service import login


def _get_token(email: str) -> str:
    pw = os.environ.get("VIGIL_SEED_PASSWORD", "vigil-dev-password")

    async def _login() -> str:
        result = await login(email, pw)
        return result.token.access_token

    return asyncio.get_event_loop().run_until_complete(_login())


# ---------------------------------------------------------------------------
# test_participants_403_for_platform_roles
# ---------------------------------------------------------------------------


def test_participants_403_for_ml_admin_role(migrated_db: dict[str, str]) -> None:
    """GET /participants/{id}/risk as ml_admin (platform_admin) → 403."""
    token = _get_token("admin@vigil.example")
    app = create_app()
    client = TestClient(app, raise_server_exceptions=False)

    participant_id = migrated_db["participant_a"]
    resp = client.get(
        f"/api/v1/participants/{participant_id}/risk",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403, (
        f"Expected 403 for ml_admin on /participants/risk, got {resp.status_code}: {resp.text}"
    )


def test_participants_403_for_auditor_role(migrated_db: dict[str, str]) -> None:
    """GET /participants/{id}/risk as auditor → 403."""
    token = _get_token("auditor@vigil.example")
    app = create_app()
    client = TestClient(app, raise_server_exceptions=False)

    participant_id = migrated_db["participant_a"]
    resp = client.get(
        f"/api/v1/participants/{participant_id}/risk",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403, (
        f"Expected 403 for auditor on /participants/risk, got {resp.status_code}: {resp.text}"
    )


def test_participants_detail_403_for_ml_admin(migrated_db: dict[str, str]) -> None:
    """GET /participants/{id} as ml_admin → 403."""
    token = _get_token("admin@vigil.example")
    app = create_app()
    client = TestClient(app, raise_server_exceptions=False)

    participant_id = migrated_db["participant_a"]
    resp = client.get(
        f"/api/v1/participants/{participant_id}",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403, (
        f"Expected 403 for ml_admin on GET /participants, got {resp.status_code}: {resp.text}"
    )


def test_participants_intervention_403_for_auditor(migrated_db: dict[str, str]) -> None:
    """POST /participants/{id}/interventions as auditor → 403 (read-only)."""
    token = _get_token("auditor@vigil.example")
    app = create_app()
    client = TestClient(app, raise_server_exceptions=False)

    participant_id = migrated_db["participant_a"]
    resp = client.post(
        f"/api/v1/participants/{participant_id}/interventions",
        json={"kind": "note", "note": "test"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert resp.status_code == 403, (
        f"Expected 403 for auditor on POST interventions, got {resp.status_code}: {resp.text}"
    )


# ---------------------------------------------------------------------------
# test_synthetic_field_present_in_cohort_row
# ---------------------------------------------------------------------------


def test_synthetic_field_present_in_cohort_row(migrated_db: dict[str, str]) -> None:
    """GET /cohort as a scoped user returns rows with `synthetic` field set."""
    token = _get_token("coord.b@vigil.example")
    app = create_app()
    client = TestClient(app, raise_server_exceptions=False)

    resp = client.get("/api/v1/cohort", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 200, (
        f"Expected 200 from /cohort, got {resp.status_code}: {resp.text}"
    )
    items = resp.json()["items"]
    # If there are rows, every row must carry the `synthetic` field.
    for row in items:
        assert "synthetic" in row, f"CohortRow missing `synthetic` field: {row}"
        assert isinstance(row["synthetic"], bool), (
            f"CohortRow.synthetic must be bool, got {type(row['synthetic'])}: {row}"
        )


def test_cohort_403_for_ml_admin_role(migrated_db: dict[str, str]) -> None:
    """GET /cohort with ml_admin token returns 403 (already in scoring leakage suite)."""
    token = _get_token("admin@vigil.example")
    app = create_app()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/api/v1/cohort", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403, (
        f"Expected 403 for ml_admin on /cohort, got {resp.status_code}: {resp.text}"
    )


def test_cohort_403_for_auditor_role(migrated_db: dict[str, str]) -> None:
    """GET /cohort with auditor token returns 403 (already in scoring leakage suite)."""
    token = _get_token("auditor@vigil.example")
    app = create_app()
    client = TestClient(app, raise_server_exceptions=False)
    resp = client.get("/api/v1/cohort", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403, (
        f"Expected 403 for auditor on /cohort, got {resp.status_code}: {resp.text}"
    )
