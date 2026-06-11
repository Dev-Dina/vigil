"""Wire-3 — participant view wired to the real scoped backend.

Exercises the endpoints the participant page now calls:
- detail (real, scope-filtered — not the old stub), risk (champion-only), risk/history
  (champion-at-each-point), interventions (real, audited round-trip).
- The sacred cross-tenant boundary holds on every wired participant endpoint.

Real Postgres via migrated_db.
"""

from __future__ import annotations

import os
import uuid

from fastapi.testclient import TestClient

from vigil.api.app import create_app

_CHAMPION_MV = "sequence_v1.0:demo"
_SHADOW_MV = "structural_v1.0:t2d"
_CARD = "data/models/t2d/model_card.md"


def _client() -> TestClient:
    return TestClient(create_app(), raise_server_exceptions=False)


def _token(client: TestClient, email: str) -> str:
    pw = os.environ.get("VIGIL_SEED_PASSWORD", "vigil-dev-password")
    r = client.post("/api/v1/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _me_user_id(client: TestClient, token: str) -> str:
    r = client.get("/api/v1/auth/me", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    return r.json()["user_id"]


def _add_score(ids: dict[str, str], pid: uuid.UUID, *, mv: str, score: float) -> None:
    from vigil.repositories import scoring as scoring_repo
    from vigil.repositories.session import sponsor_bootstrap_session

    with sponsor_bootstrap_session(ids["sponsor_a"]) as session:
        scoring_repo.append_score(
            session,
            participant_id=pid,
            sponsor_id=uuid.UUID(ids["sponsor_a"]),
            trial_id=uuid.UUID(ids["trial_a"]),
            site_id=uuid.UUID(ids["site_a"]),
            risk_score=score,
            risk_band="high" if score >= 0.7 else "low",
            top_factors=[],
            reasons=[],
            model_version=mv,
            model_card_ref=_CARD,
            synthetic=True,
        )


def _h(client: TestClient, token: str, path: str):  # type: ignore[no-untyped-def]
    return client.get(path, headers={"Authorization": f"Bearer {token}"})


# ---------------------------------------------------------------------------
# 1. Detail is REAL (scoped DB read, not the old stub)
# ---------------------------------------------------------------------------


def test_participant_detail_is_real(migrated_db: dict[str, str]) -> None:
    ids = migrated_db
    client = _client()
    token = _token(client, "coord.a@vigil.example")
    r = _h(client, token, f"/api/v1/participants/{ids['participant_a']}")
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["trial_id"] == ids["trial_a"], (
        f"detail.trial_id {body['trial_id']!r} != real {ids['trial_a']!r} (still stub?)"
    )
    assert body["site_id"] == ids["site_a"]
    assert body["trial_id"] != "trl_stub" and body["site_id"] != "sit_stub"
    # enrolled_at is a real timestamp (the source for daysEnrolled), not the stub 2026-01-01.
    assert not body["enrolled_at"].startswith("2026-01-01"), (
        "enrolled_at looks like the stub constant — daysEnrolled would be fabricated"
    )
    # Coordinator is a site role → identity (coded_ref) populated.
    assert body["identity"] is not None and body["identity"]["coded_ref"], (
        "site role should receive identity.coded_ref"
    )


# ---------------------------------------------------------------------------
# 2. Champion-only on the wired participant path (shadow never surfaces)
# ---------------------------------------------------------------------------


def test_participant_risk_and_history_champion_only(
    migrated_db: dict[str, str],
) -> None:
    ids = migrated_db
    pid = uuid.UUID(ids["participant_a"])
    # participant_a has a seeded champion score; add an adversarial shadow row.
    _add_score(ids, pid, mv=_CHAMPION_MV, score=0.66)
    _add_score(ids, pid, mv=_SHADOW_MV, score=0.99)

    client = _client()
    token = _token(client, "coord.a@vigil.example")

    risk = _h(client, token, f"/api/v1/participants/{pid}/risk")
    assert risk.status_code == 200, risk.text
    assert risk.json()["model_version"] == _CHAMPION_MV, (
        f"/risk surfaced {risk.json()['model_version']!r} — shadow must never reach the view"
    )

    hist = _h(client, token, f"/api/v1/participants/{pid}/risk/history")
    assert hist.status_code == 200, hist.text
    versions = {p["model_version"] for p in hist.json()["points"]}
    assert _SHADOW_MV not in versions, f"shadow leaked into /risk/history: {versions}"
    assert versions <= {_CHAMPION_MV}, f"non-champion in history: {versions}"


# ---------------------------------------------------------------------------
# 3. Cross-tenant boundary holds on every wired participant endpoint
# ---------------------------------------------------------------------------


def test_participant_cross_tenant_blocked(migrated_db: dict[str, str]) -> None:
    ids = migrated_db
    pid = ids["participant_a"]
    client = _client()
    token_b = _token(client, "coord.b@vigil.example")

    for path in (
        f"/api/v1/participants/{pid}",
        f"/api/v1/participants/{pid}/risk",
        f"/api/v1/participants/{pid}/risk/history",
        f"/api/v1/participants/{pid}/interventions",
    ):
        r = _h(client, token_b, path)
        assert r.status_code in (403, 404), (
            f"Sponsor B reached {path} with {r.status_code} — cross-tenant leak: {r.text}"
        )

    # POST intervention cross-tenant must also fail closed.
    rp = client.post(
        f"/api/v1/participants/{pid}/interventions",
        json={"kind": "call", "note": "x"},
        headers={"Authorization": f"Bearer {token_b}"},
    )
    assert rp.status_code in (403, 404), (
        f"Sponsor B logged an intervention on Sponsor A's participant: {rp.status_code}"
    )


# ---------------------------------------------------------------------------
# 4. Intervention round-trip: POST (real, audited, real actor) → GET returns it
# ---------------------------------------------------------------------------


def test_intervention_round_trip(migrated_db: dict[str, str]) -> None:
    ids = migrated_db
    pid = ids["participant_a"]
    client = _client()
    token = _token(client, "coord.a@vigil.example")
    actor = _me_user_id(client, token)

    rp = client.post(
        f"/api/v1/participants/{pid}/interventions",
        json={"kind": "call", "note": "Wire-3 outreach"},
        headers={"Authorization": f"Bearer {token}"},
    )
    assert rp.status_code == 201, rp.text
    out = rp.json()
    assert out["id"] != "iv_stub", "intervention id is still the stub — not persisted"
    assert out["actor_user_id"] == actor, (
        f"actor {out['actor_user_id']!r} != authenticated user {actor!r} "
        "(actor must be server-side, not client-supplied)"
    )

    rg = _h(client, token, f"/api/v1/participants/{pid}/interventions")
    assert rg.status_code == 200, rg.text
    items = rg.json()["items"]
    assert any(i["id"] == out["id"] for i in items), (
        "logged intervention not returned by GET — not persisted"
    )

    # Audit row written (server-side, nothing changes silently).
    from sqlalchemy import select

    from vigil.db.models import AuditLog
    from vigil.repositories.session import sponsor_bootstrap_session

    with sponsor_bootstrap_session(ids["sponsor_a"]) as session:
        audits = (
            session.execute(
                select(AuditLog).where(
                    AuditLog.action == "intervention_logged",
                    AuditLog.target_id == out["id"],
                )
            )
            .scalars()
            .all()
        )
    assert audits, "no audit_log row for the logged intervention"
