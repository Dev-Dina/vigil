"""End-to-end API path: login → bearer → /me → /cohort, with RLS isolation over HTTP.

Exercises the full spine: router → auth dependency (token verify + live Redis session +
rate limit) → service → scope-bound repository → RLS. Requires Postgres + Redis; skips if
either is unreachable (CI provides both).
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("VIGIL_SECRETS_BACKEND", "env")
os.environ.setdefault("VIGIL_JWT_SIGNING_KEY", "test-signing-key-not-a-secret")
os.environ.setdefault("VIGIL_LLM_API_KEY", "test")
os.environ.setdefault("VIGIL_REDIS_URL", "redis://localhost:6379/15")

SEED_PW = os.environ.get("VIGIL_SEED_PASSWORD", "vigil-dev-password")


def _redis_reachable() -> bool:
    import redis as sync_redis

    try:
        sync_redis.from_url(os.environ["VIGIL_REDIS_URL"]).ping()
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.fixture
def client(migrated_db):
    if not _redis_reachable():
        pytest.skip("no Redis reachable for the API path test")
    from fastapi.testclient import TestClient

    from vigil.api.app import create_app

    return TestClient(create_app())


def _login(client, email: str) -> str:
    resp = client.post("/api/v1/auth/login", json={"email": email, "password": SEED_PW})
    assert resp.status_code == 200, resp.text
    return resp.json()["access_token"]


def test_login_me_and_scoped_cohort(client, migrated_db):
    ids = migrated_db
    token = _login(client, "coord.b@vigil.example")
    headers = {"Authorization": f"Bearer {token}"}

    me = client.get("/api/v1/auth/me", headers=headers)
    assert me.status_code == 200
    body = me.json()
    assert body["role"] == "coordinator"
    assert body["home_sponsor_id"] == ids["sponsor_b"]

    cohort = client.get("/api/v1/cohort", headers=headers)
    assert cohort.status_code == 200
    rows = cohort.json()["items"]
    # B only ever sees B's participant; A's never appears (RLS over the full HTTP path).
    assert all(r["trial_id"] == ids["trial_b"] for r in rows)
    assert any(r["participant_id"] == ids["participant_b"] for r in rows)
    assert all(r["participant_id"] != ids["participant_a"] for r in rows)


def test_all_seven_roles_login_and_me(client, migrated_db):
    ids = migrated_db
    # (email, expected role, expected home_sponsor_id key | None)
    cases = [
        ("admin@vigil.example", "platform_admin", None),
        ("auditor@vigil.example", "auditor", None),
        ("oversight.a@vigil.example", "sponsor_oversight", "sponsor_a"),
        ("coord.a@vigil.example", "coordinator", "sponsor_a"),
        ("pi.a@vigil.example", "principal_investigator", "sponsor_a"),
        ("cro.manager@vigil.example", "study_manager", None),
        ("cra@vigil.example", "cra", None),
    ]
    seen_roles: set[str] = set()
    for email, expected_role, sponsor_key in cases:
        token = _login(client, email)
        me = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
        assert me.status_code == 200, (email, me.text)
        body = me.json()
        assert body["role"] == expected_role, email
        if sponsor_key is not None:
            assert body["home_sponsor_id"] == ids[sponsor_key], email
        else:
            assert body["home_sponsor_id"] is None, email
        # CRO roles carry no home sponsor but a home CRO.
        if expected_role in {"study_manager", "cra"}:
            assert body["home_cro_id"] == ids["cro_id"], email
        seen_roles.add(body["role"])
    assert seen_roles == {
        "platform_admin",
        "auditor",
        "sponsor_oversight",
        "coordinator",
        "principal_investigator",
        "study_manager",
        "cra",
    }


def test_logout_revokes_session(client):
    token = _login(client, "coord.a@vigil.example")
    headers = {"Authorization": f"Bearer {token}"}
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 200
    assert client.post("/api/v1/auth/logout", headers=headers).status_code == 204
    # Token rejected after logout even though it has not expired.
    assert client.get("/api/v1/auth/me", headers=headers).status_code == 401


def test_unauthenticated_is_rejected(client):
    assert client.get("/api/v1/auth/me").status_code == 401
    assert client.get("/api/v1/cohort").status_code == 401
