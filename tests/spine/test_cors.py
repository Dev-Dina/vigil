"""CORS: the browser preflight + cross-origin requests from the frontend origin are allowed.

The frontend (http://localhost:3000) calls the API cross-origin; the browser sends a preflight
OPTIONS that previously hit routers with no OPTIONS handler → 405, blocking login + data fetches.
These tests assert CORSMiddleware now answers the preflight and stamps the allow-origin header.

No DB needed — the preflight is short-circuited by CORSMiddleware before any router/handler runs,
and /healthz touches no DB. (The spine conftest provides the env so create_app() instantiates.)
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from vigil.api.app import create_app

_FRONTEND_ORIGINS = ("http://localhost:3000", "http://127.0.0.1:3000")


def _client() -> TestClient:
    return TestClient(create_app(), raise_server_exceptions=False)


def test_preflight_options_not_405_and_allows_frontend_origin() -> None:
    """OPTIONS preflight to a real API route returns 200/204 (not 405) with the allow-origin echo."""
    client = _client()
    for origin in _FRONTEND_ORIGINS:
        r = client.options(
            "/api/v1/auth/login",
            headers={
                "Origin": origin,
                "Access-Control-Request-Method": "POST",
                "Access-Control-Request-Headers": "authorization,content-type",
            },
        )
        assert r.status_code in (200, 204), (
            f"preflight from {origin} returned {r.status_code} (CORS not answering the OPTIONS)"
        )
        assert r.headers.get("access-control-allow-origin") == origin
        assert r.headers.get("access-control-allow-credentials") == "true"


def test_actual_request_carries_cors_headers_for_frontend_origin() -> None:
    """A real GET with the frontend Origin is permitted and carries the allow-origin header."""
    client = _client()
    for origin in _FRONTEND_ORIGINS:
        r = client.get("/healthz", headers={"Origin": origin})
        assert r.status_code == 200
        assert r.headers.get("access-control-allow-origin") == origin
        assert r.headers.get("access-control-allow-credentials") == "true"


def test_post_route_preflight_allowed() -> None:
    """The cohort + scoring routes the browser hits also clear preflight (representative POST/GET)."""
    client = _client()
    for path in ("/api/v1/cohort", "/api/v1/scoring/trigger"):
        r = client.options(
            path,
            headers={
                "Origin": "http://localhost:3000",
                "Access-Control-Request-Method": "POST",
            },
        )
        assert r.status_code in (200, 204), f"{path} preflight returned {r.status_code}"
        assert r.headers.get("access-control-allow-origin") == "http://localhost:3000"


def test_unlisted_origin_is_not_allowed() -> None:
    """An origin outside the allow-list gets NO allow-origin echo (the list is not '*')."""
    client = _client()
    r = client.get("/healthz", headers={"Origin": "http://evil.example"})
    # The request itself still returns (CORS is browser-enforced), but the browser-trusted
    # allow-origin header must NOT echo the disallowed origin.
    assert r.headers.get("access-control-allow-origin") != "http://evil.example"
