"""Gate FIX-B2 (C4) — job-poll endpoints distinguish an infra outage (503) from not-found (404).

A Redis/job-store outage must NOT be masqueraded as a 404 (the old fail-open: broad ``except`` →
``return None`` → 404). It now surfaces as 503 Service Unavailable. A genuinely absent job stays 404.
Covers BOTH poll endpoints: GET /assistant/jobs/{id} and GET /scoring/jobs/{id}. Requires Postgres +
Redis (the not-found path runs against real Redis); the infra path is simulated deterministically by
making ``arq.create_pool`` raise a Redis ConnectionError — no real outage needed.
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
        pytest.skip("no Redis reachable for the job-poll API test")
    from fastapi.testclient import TestClient

    from vigil.api.app import create_app

    return TestClient(create_app())


def _auth_headers(client) -> dict[str, str]:
    resp = client.post(
        "/api/v1/auth/login",
        json={"email": "coord.a@vigil.example", "password": SEED_PW},
    )
    assert resp.status_code == 200, resp.text
    return {"Authorization": f"Bearer {resp.json()['access_token']}"}


def test_job_poll_infra_error_is_503_not_404(client, monkeypatch):
    # Simulate a Redis/job-store outage: arq.create_pool raises a Redis ConnectionError. The service
    # must surface ServiceUnavailableError → the router maps it to 503, NEVER a 404 (which would hide
    # the outage as "job not found").
    import arq
    from redis.exceptions import ConnectionError as RedisConnectionError

    async def _boom(*args, **kwargs):
        raise RedisConnectionError("redis is down")

    monkeypatch.setattr(arq, "create_pool", _boom)
    headers = _auth_headers(client)

    r_assistant = client.get("/api/v1/assistant/jobs/any-job-id", headers=headers)
    assert r_assistant.status_code == 503, r_assistant.text

    r_scoring = client.get("/api/v1/scoring/jobs/any-job-id", headers=headers)
    assert r_scoring.status_code == 503, r_scoring.text


def test_job_poll_genuine_not_found_is_404(client):
    # Real Redis is up; an unknown job id is a legitimate not-found → 404 (UNCHANGED behaviour).
    headers = _auth_headers(client)

    r_assistant = client.get(
        "/api/v1/assistant/jobs/nonexistent-job-id", headers=headers
    )
    assert r_assistant.status_code == 404, r_assistant.text

    r_scoring = client.get("/api/v1/scoring/jobs/nonexistent-job-id", headers=headers)
    assert r_scoring.status_code == 404, r_scoring.text
