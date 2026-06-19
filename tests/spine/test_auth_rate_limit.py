"""Gate SEC-RL — per-account brute-force throttle on POST /auth/login.

Proves the credential-stuffing gap is closed: repeated FAILED logins for one account are throttled
to 429 (with Retry-After) instead of being unbounded; a legitimate successful login is never blocked
and resets the counter; the window/limit are configurable (so the test is deterministic — no real
sleeps). Reuses the existing Redis fixed-window limiter (`vigil/core/rate_limit.py`) — asserted by
the `rl:login:acct:*` key it writes. (Throttling is per ACCOUNT, not per IP — see the router note;
shared corporate NAT makes a per-IP bucket a false-positive risk.)

Requires Postgres + Redis (same harness as test_api_auth); skips if either is unreachable.
"""

from __future__ import annotations

import os

import pytest

os.environ.setdefault("VIGIL_SECRETS_BACKEND", "env")
os.environ.setdefault("VIGIL_JWT_SIGNING_KEY", "test-signing-key-not-a-secret")
os.environ.setdefault("VIGIL_LLM_API_KEY", "test")
os.environ.setdefault("VIGIL_REDIS_URL", "redis://localhost:6379/15")

SEED_PW = os.environ.get("VIGIL_SEED_PASSWORD", "vigil-dev-password")
_VICTIM = "coord.a@vigil.example"
_ACCT_KEY = f"rl:login:acct:{_VICTIM.lower()}"


def _redis():
    import redis as sync_redis

    return sync_redis.from_url(os.environ["VIGIL_REDIS_URL"])


def _redis_reachable() -> bool:
    try:
        _redis().ping()
        return True
    except Exception:  # noqa: BLE001
        return False


def _clear_login_keys() -> None:
    _redis().delete(_ACCT_KEY)


@pytest.fixture
def client(migrated_db, monkeypatch):
    if not _redis_reachable():
        pytest.skip("no Redis reachable for the auth-throttle test")
    from fastapi.testclient import TestClient

    from vigil.api.app import create_app
    from vigil.core.config import get_settings

    # Small, deterministic limit/window (singleton is mutated; monkeypatch restores afterwards).
    monkeypatch.setattr(get_settings(), "login_rate_limit_max_failures", 3)
    monkeypatch.setattr(get_settings(), "login_rate_limit_window_seconds", 300)
    _clear_login_keys()
    yield TestClient(create_app())
    _clear_login_keys()


def _bad_login(client):
    return client.post(
        "/api/v1/auth/login", json={"email": _VICTIM, "password": "wrong-password"}
    )


def _good_login(client):
    return client.post(
        "/api/v1/auth/login", json={"email": _VICTIM, "password": SEED_PW}
    )


def test_brute_force_login_is_throttled_with_retry_after(client):
    # limit=3 → 3 failed attempts are allowed (each an honest 401, NOT 500).
    for i in range(3):
        r = _bad_login(client)
        assert r.status_code == 401, f"attempt {i + 1}: {r.status_code} {r.text}"

    # The 4th attempt is throttled BEFORE any credential check → 429 + Retry-After.
    r = _bad_login(client)
    assert r.status_code == 429, r.text
    assert "retry-after" in {k.lower() for k in r.headers}
    assert int(r.headers["retry-after"]) > 0

    # Even the CORRECT password is now refused (the account is locked for the window) — proves it
    # actually throttles, never a silent 500 or a leaked auth.
    r = _good_login(client)
    assert r.status_code == 429, r.text

    # Reuses the existing Redis limiter: the fixed-window account key exists.
    assert _redis().exists(_ACCT_KEY)

    # After the window resets (here: the configurable reset / key expiry, simulated) it's allowed.
    _clear_login_keys()
    assert _good_login(client).status_code == 200


def test_successful_login_resets_failures_so_legit_user_not_locked_out(client):
    # Two failures (under the limit of 3) …
    assert _bad_login(client).status_code == 401
    assert _bad_login(client).status_code == 401
    # … then a correct login still works (within budget) AND clears the account counter.
    assert _good_login(client).status_code == 200
    # So the next failure starts the budget fresh — one bad attempt is a 401, not an instant 429.
    assert _bad_login(client).status_code == 401


def test_throttle_disabled_when_max_failures_zero(client, monkeypatch):
    from vigil.core.config import get_settings

    monkeypatch.setattr(get_settings(), "login_rate_limit_max_failures", 0)
    _clear_login_keys()
    # With the throttle disabled, many failures never escalate to 429 (each stays a plain 401).
    for _ in range(6):
        assert _bad_login(client).status_code == 401
