"""H2 — GET /participants/{id}/risk/history (champion trajectory, scope-filtered).

Reads the H1 appended history as a champion-only risk trajectory:
- time-ordered points; champion-only (no shadow/challenger point ever appears);
- cross-tenant boundary holds (Sponsor B → 404 on Sponsor A's participant);
- per-point provenance (synthetic flag), both directions; 404 when no champion history.

Real Postgres via migrated_db. Fresh participants + explicit computed_at for deterministic order.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from vigil.api.app import create_app

_CHAMPION_MV = "sequence_v1.1:demo"
_SHADOW_MV = "structural_v1.0:t2d"
_CHALLENGER_MV = "challenger_v0:test"  # not a champion → must never surface
_CARD = "data/models/t2d/model_card.md"


def _client() -> TestClient:
    return TestClient(create_app(), raise_server_exceptions=False)


def _token(client: TestClient, email: str) -> str:
    pw = os.environ.get("VIGIL_SEED_PASSWORD", "vigil-dev-password")
    r = client.post("/api/v1/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _fresh_participant(ids: dict[str, str], coded_ref: str) -> uuid.UUID:
    from vigil.db.models import Participant
    from vigil.repositories.session import sponsor_bootstrap_session

    pid = uuid.uuid4()
    with sponsor_bootstrap_session(ids["sponsor_a"]) as session:
        session.add(
            Participant(
                id=pid,
                sponsor_id=uuid.UUID(ids["sponsor_a"]),
                trial_id=uuid.UUID(ids["trial_a"]),
                site_id=uuid.UUID(ids["site_a"]),
                coded_ref=coded_ref,
            )
        )
        session.flush()
    return pid


def _insert_score(
    ids: dict[str, str],
    pid: uuid.UUID,
    *,
    risk_score: float,
    model_version: str,
    synthetic: bool,
    when: datetime,
) -> None:
    from vigil.db.models import ParticipantScore
    from vigil.repositories.session import sponsor_bootstrap_session

    with sponsor_bootstrap_session(ids["sponsor_a"]) as session:
        row = ParticipantScore(
            participant_id=pid,
            sponsor_id=uuid.UUID(ids["sponsor_a"]),
            trial_id=uuid.UUID(ids["trial_a"]),
            site_id=uuid.UUID(ids["site_a"]),
            risk_score=risk_score,
            risk_band="high" if risk_score >= 0.7 else "low",
            top_factors=[],
            reasons=[],
            model_version=model_version,
            model_card_ref=_CARD,
            synthetic=synthetic,
        )
        row.computed_at = when
        session.add(row)
        session.flush()


def _history(client: TestClient, token: str, pid: uuid.UUID):  # type: ignore[no-untyped-def]
    return client.get(
        f"/api/v1/participants/{pid}/risk/history",
        headers={"Authorization": f"Bearer {token}"},
    )


# ---------------------------------------------------------------------------
# 1. Trajectory returned in time order
# ---------------------------------------------------------------------------


def test_trajectory_returned_time_ordered(migrated_db: dict[str, str]) -> None:
    pid = _fresh_participant(migrated_db, "h2-traj")
    base = datetime.now(tz=timezone.utc)
    _insert_score(
        migrated_db,
        pid,
        risk_score=0.20,
        model_version=_CHAMPION_MV,
        synthetic=True,
        when=base,
    )
    _insert_score(
        migrated_db,
        pid,
        risk_score=0.50,
        model_version=_CHAMPION_MV,
        synthetic=True,
        when=base + timedelta(hours=1),
    )
    _insert_score(
        migrated_db,
        pid,
        risk_score=0.80,
        model_version=_CHAMPION_MV,
        synthetic=True,
        when=base + timedelta(hours=2),
    )

    client = _client()
    r = _history(client, _token(client, "coord.a@vigil.example"), pid)
    assert r.status_code == 200, r.text
    points = r.json()["points"]
    assert len(points) == 3, f"expected 3 champion history points, got {len(points)}"
    scores = [p["risk_score"] for p in points]
    assert scores == sorted(scores), f"points not ordered by computed_at ASC: {scores}"
    assert abs(scores[0] - 0.20) < 1e-6 and abs(scores[-1] - 0.80) < 1e-6


# ---------------------------------------------------------------------------
# 2. Champion-only: shadow/challenger points NEVER appear (critical)
# ---------------------------------------------------------------------------


def test_history_champion_only(migrated_db: dict[str, str]) -> None:
    pid = _fresh_participant(migrated_db, "h2-champ-only")
    base = datetime.now(tz=timezone.utc)
    _insert_score(
        migrated_db,
        pid,
        risk_score=0.30,
        model_version=_CHAMPION_MV,
        synthetic=True,
        when=base,
    )
    _insert_score(
        migrated_db,
        pid,
        risk_score=0.60,
        model_version=_CHAMPION_MV,
        synthetic=True,
        when=base + timedelta(hours=2),
    )
    # Non-champion points interleaved with LATER timestamps — must be excluded.
    _insert_score(
        migrated_db,
        pid,
        risk_score=0.99,
        model_version=_SHADOW_MV,
        synthetic=True,
        when=base + timedelta(hours=1),
    )
    _insert_score(
        migrated_db,
        pid,
        risk_score=0.01,
        model_version=_CHALLENGER_MV,
        synthetic=True,
        when=base + timedelta(hours=3),
    )

    client = _client()
    r = _history(client, _token(client, "coord.a@vigil.example"), pid)
    assert r.status_code == 200, r.text
    points = r.json()["points"]
    versions = {p["model_version"] for p in points}
    assert versions == {_CHAMPION_MV}, (
        f"non-champion points leaked into the trajectory: {versions}"
    )
    assert len(points) == 2, f"expected 2 champion points, got {len(points)}"


# ---------------------------------------------------------------------------
# 3. Cross-tenant boundary holds on the new read path
# ---------------------------------------------------------------------------


def test_history_cross_tenant_blocked(migrated_db: dict[str, str]) -> None:
    pid = _fresh_participant(migrated_db, "h2-isolation")
    base = datetime.now(tz=timezone.utc)
    _insert_score(
        migrated_db,
        pid,
        risk_score=0.40,
        model_version=_CHAMPION_MV,
        synthetic=True,
        when=base,
    )

    client = _client()
    # Sponsor A sees the history.
    ra = _history(client, _token(client, "coord.a@vigil.example"), pid)
    assert ra.status_code == 200, ra.text
    # Sponsor B must NOT (RLS renders A's participant as no rows → 404 fail-closed).
    rb = _history(client, _token(client, "coord.b@vigil.example"), pid)
    assert rb.status_code == 404, (
        f"Sponsor B got {rb.status_code} on Sponsor A's history — cross-tenant leak: {rb.text}"
    )


# ---------------------------------------------------------------------------
# 4. Provenance per point — synthetic flag both directions
# ---------------------------------------------------------------------------


def test_history_provenance_per_point(migrated_db: dict[str, str]) -> None:
    pid = _fresh_participant(migrated_db, "h2-provenance")
    base = datetime.now(tz=timezone.utc)
    _insert_score(
        migrated_db,
        pid,
        risk_score=0.30,
        model_version=_CHAMPION_MV,
        synthetic=True,
        when=base,
    )
    _insert_score(
        migrated_db,
        pid,
        risk_score=0.40,
        model_version=_CHAMPION_MV,
        synthetic=False,
        when=base + timedelta(hours=1),
    )

    client = _client()
    r = _history(client, _token(client, "coord.a@vigil.example"), pid)
    assert r.status_code == 200, r.text
    points = r.json()["points"]
    assert [p["synthetic"] for p in points] == [True, False], (
        f"per-point provenance not surfaced honestly: {[p['synthetic'] for p in points]}"
    )


# ---------------------------------------------------------------------------
# 5. No champion history → 404 (honest, not a fabricated point)
# ---------------------------------------------------------------------------


def test_history_empty_in_scope_is_200_empty(migrated_db: dict[str, str]) -> None:
    """In-scope participant with no champion points → 200 with empty list (H2b: data state,
    not a security 404). Only a shadow point exists → no champion-of-record points."""
    pid = _fresh_participant(migrated_db, "h2-empty")
    _insert_score(
        migrated_db,
        pid,
        risk_score=0.50,
        model_version=_SHADOW_MV,
        synthetic=True,
        when=datetime.now(tz=timezone.utc),
    )
    client = _client()
    r = _history(client, _token(client, "coord.a@vigil.example"), pid)
    assert r.status_code == 200, (
        f"expected 200 empty for in-scope-no-history, got {r.status_code}: {r.text}"
    )
    assert r.json()["points"] == [], f"expected empty points, got {r.json()['points']}"


# ---------------------------------------------------------------------------
# 6. Platform role denied (403)
# ---------------------------------------------------------------------------


def test_history_platform_role_denied(migrated_db: dict[str, str]) -> None:
    pid = uuid.UUID(migrated_db["participant_a"])
    client = _client()
    r = _history(client, _token(client, "admin@vigil.example"), pid)
    assert r.status_code == 403, (
        f"platform_admin not denied on history: {r.status_code}"
    )
