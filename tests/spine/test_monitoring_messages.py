"""Gate 6.1 — GET /monitoring/messages inspect API (the sacred observability read surface).

Proves the 6.0 contract: PLATFORM/AUDITOR-ONLY (403 for every sponsor/site role), RLS-bound
(cross-tenant-by-role read for platform/auditor; the message_events RLS narrows any non-platform
scope as a backstop), and REDACTED-ONLY (no raw content reachable).

Real Postgres via migrated_db; HTTP-level for the role gate, repo-level for the RLS backstop.
"""

from __future__ import annotations

import os
import uuid

from fastapi.testclient import TestClient

from vigil.api.app import create_app
from vigil.repositories import observability as obs_repo
from vigil.repositories.session import (
    platform_session,
    sponsor_bootstrap_session,
)


def _client() -> TestClient:
    return TestClient(create_app(), raise_server_exceptions=False)


def _token(client: TestClient, email: str) -> str:
    pw = os.environ.get("VIGIL_SEED_PASSWORD", "vigil-dev-password")
    r = client.post("/api/v1/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _seed_event(
    ids: dict[str, str],
    *,
    sponsor_key: str | None,
    request_id: str,
    decision: str = "allowed",
    status: str = "ok",
) -> None:
    """Write one message_events row under the right scope (sponsor row, or null-sponsor=platform)."""
    if sponsor_key is None:
        with platform_session() as session:
            obs_repo.write_message_event(
                session,
                conversation_id=uuid.uuid4(),
                request_id=request_id,
                sponsor_id=None,
                role_or_guest_scope="guest",
                surface="public_guide",
                guardrail_decision=decision,
                status=status,
                redacted_user_msg=f"[REDACTED] {request_id}",
                redacted_assistant_msg="[REDACTED] resp",
            )
    else:
        sid = ids[sponsor_key]
        with sponsor_bootstrap_session(sid) as session:
            obs_repo.write_message_event(
                session,
                conversation_id=uuid.uuid4(),
                request_id=request_id,
                sponsor_id=uuid.UUID(sid),
                role_or_guest_scope="coordinator",
                surface="local_assistant",
                guardrail_decision=decision,
                status=status,
                redacted_user_msg=f"[REDACTED] {request_id}",
                redacted_assistant_msg="[REDACTED] resp",
            )


def _req_ids(payload: dict) -> set[str]:
    return {row["request_id"] for row in payload["items"]}


# ---------------------------------------------------------------------------
# 1. ROLE GATE (primary sacred check): platform/auditor 200; everyone else 403
# ---------------------------------------------------------------------------


def test_role_gate_platform_auditor_only(migrated_db: dict[str, str]) -> None:
    client = _client()
    for email in ("admin@vigil.example", "auditor@vigil.example"):
        r = client.get("/api/v1/monitoring/messages", headers=_h(_token(client, email)))
        assert r.status_code == 200, (
            f"{email} should reach inspect: {r.status_code} {r.text}"
        )

    for email in (
        "coord.a@vigil.example",  # coordinator (site)
        "pi.a@vigil.example",  # PI (site)
        "cra@vigil.example",  # CRA (CRO site-scoped)
        "oversight.a@vigil.example",  # sponsor oversight
        "cro.manager@vigil.example",  # study manager (CRO)
    ):
        r = client.get("/api/v1/monitoring/messages", headers=_h(_token(client, email)))
        assert r.status_code == 403, (
            f"{email} must be DENIED the inspect surface (got {r.status_code})"
        )


# ---------------------------------------------------------------------------
# 2. CROSS-TENANT-BY-ROLE: platform/auditor sees events across sponsors
# ---------------------------------------------------------------------------


def test_platform_auditor_sees_across_sponsors(migrated_db: dict[str, str]) -> None:
    ids = migrated_db
    _seed_event(ids, sponsor_key="sponsor_a", request_id="evt-A-6.1")
    _seed_event(ids, sponsor_key="sponsor_b", request_id="evt-B-6.1")
    _seed_event(ids, sponsor_key=None, request_id="evt-GUIDE-6.1")  # null-sponsor

    client = _client()
    for email in ("admin@vigil.example", "auditor@vigil.example"):
        r = client.get(
            "/api/v1/monitoring/messages?limit=500", headers=_h(_token(client, email))
        )
        assert r.status_code == 200, r.text
        seen = _req_ids(r.json())
        assert {"evt-A-6.1", "evt-B-6.1", "evt-GUIDE-6.1"} <= seen, (
            f"{email} (cross-tenant-by-role) must see A + B + Guide events; saw {seen}"
        )


# ---------------------------------------------------------------------------
# 3. RLS BACKSTOP: a non-platform scope reaching the query is narrowed to own sponsor
# ---------------------------------------------------------------------------


def test_rls_backstop_narrows_non_platform_scope(migrated_db: dict[str, str]) -> None:
    ids = migrated_db
    _seed_event(ids, sponsor_key="sponsor_a", request_id="evt-A-backstop")
    _seed_event(ids, sponsor_key="sponsor_b", request_id="evt-B-backstop")

    # If the query path ran under a Sponsor-B-bound session (role gate aside), the
    # message_events RLS admits ONLY B's rows — never A's. (Defence in depth behind the 403.)
    with sponsor_bootstrap_session(ids["sponsor_b"]) as session:
        rows = obs_repo.query_message_events(session, limit=500)
        seen = {r.request_id for r in rows}
    assert "evt-B-backstop" in seen, "Sponsor B must see its own event under RLS"
    assert "evt-A-backstop" not in seen, (
        "RLS backstop breached: a Sponsor-B scope saw a Sponsor-A event"
    )


# ---------------------------------------------------------------------------
# 4. REDACTED-ONLY + filters
# ---------------------------------------------------------------------------


def test_redacted_only_and_filters(migrated_db: dict[str, str]) -> None:
    ids = migrated_db
    _seed_event(
        ids, sponsor_key="sponsor_a", request_id="evt-allowed-6.1", decision="allowed"
    )
    _seed_event(
        ids,
        sponsor_key="sponsor_a",
        request_id="evt-blocked-6.1",
        decision="blocked",
        status="refused",
    )

    client = _client()
    tok = _token(client, "auditor@vigil.example")

    # Redacted-only: rows carry redacted fields; no raw-content key exists in the shape.
    r = client.get("/api/v1/monitoring/messages?limit=500", headers=_h(tok))
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    assert items, "expected events"
    sample = items[0]
    assert "redacted_user_msg" in sample and "redacted_assistant_msg" in sample
    assert "user_msg" not in sample and "assistant_msg" not in sample  # no raw fields
    assert all(
        row["redacted_user_msg"].startswith("[REDACTED]")
        for row in items
        if row["request_id"].startswith("evt-")
    )

    # Filter: guardrail_decision=blocked → only blocked turns.
    rb = client.get(
        "/api/v1/monitoring/messages?guardrail_decision=blocked&limit=500",
        headers=_h(tok),
    )
    assert rb.status_code == 200, rb.text
    decisions = {row["guardrail_decision"] for row in rb.json()["items"]}
    assert decisions <= {"blocked"}, (
        f"blocked filter leaked other decisions: {decisions}"
    )
    assert "evt-blocked-6.1" in _req_ids(rb.json())
    assert "evt-allowed-6.1" not in _req_ids(rb.json())
