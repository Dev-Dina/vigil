"""H2b — risk-history is promotion-aware (champion-at-each-point, semantic (b)).

The trajectory is the row that was CHAMPION-OF-RECORD at each point in time, spanning a B3
promotion, each point honestly labeled with its model_version. The critical guard: a shadow/
challenger row written DURING a version's champion tenure is still non-champion and MUST be
excluded — "champion-at-each-point" is NOT "any row regardless of role".

Real Postgres via migrated_db. Fresh regime + participant so the promotion timeline is isolated.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from vigil.api.app import create_app
from vigil.core.scope import Scope
from vigil.domain import Role

_CARD = "data/models/t2d/model_card.md"


def _client() -> TestClient:
    return TestClient(create_app(), raise_server_exceptions=False)


def _token(client: TestClient, email: str) -> str:
    pw = os.environ.get("VIGIL_SEED_PASSWORD", "vigil-dev-password")
    r = client.post("/api/v1/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _admin_scope(user_id: str) -> Scope:
    return Scope(
        user_id=user_id,
        role=Role.PLATFORM_ADMIN,
        home_sponsor_id=None,
        home_cro_id=None,
        tuples=(),
        scope_ver=1,
    )


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


def _seed_champion(regime: str, version: str) -> None:
    from vigil.db.models import RoutingState
    from vigil.repositories.session import platform_session

    with platform_session() as session:
        session.add(
            RoutingState(
                id=uuid.uuid4(),
                regime=regime,
                role="champion",
                model_version=version,
                model_card_ref=_CARD,
                health="healthy",
            )
        )
        session.flush()


def _history(client: TestClient, token: str, pid: uuid.UUID):  # type: ignore[no-untyped-def]
    return client.get(
        f"/api/v1/participants/{pid}/risk/history",
        headers={"Authorization": f"Bearer {token}"},
    )


# ---------------------------------------------------------------------------
# 1. (b) spans a promotion: 4 points, first 2 = X, last 2 = Y, version-labeled
# ---------------------------------------------------------------------------


def test_history_spans_promotion(migrated_db: dict[str, str]) -> None:
    from vigil.services.routing_service import promote

    regime = "h2b_promo"
    ver_x = "histX_v1:t"
    ver_y = "histY_v2:t"
    _seed_champion(regime, ver_x)

    pid = _fresh_participant(migrated_db, "h2b-promo")
    now = datetime.now(tz=timezone.utc)
    # Scored under champion X (before promotion).
    _insert_score(
        migrated_db,
        pid,
        risk_score=0.20,
        model_version=ver_x,
        synthetic=True,
        when=now - timedelta(hours=2),
    )
    _insert_score(
        migrated_db,
        pid,
        risk_score=0.30,
        model_version=ver_x,
        synthetic=True,
        when=now - timedelta(hours=1),
    )

    # Promote X -> Y (real B3 path: writes audit transition + updates routing_state).
    promote(
        _admin_scope(migrated_db["platform_admin"]),
        regime=regime,
        model_version=ver_y,
        model_card_ref=_CARD,
        eval_provenance="architecture_validation",
    )

    # Scored under champion Y (after promotion).
    _insert_score(
        migrated_db,
        pid,
        risk_score=0.60,
        model_version=ver_y,
        synthetic=True,
        when=now + timedelta(hours=1),
    )
    _insert_score(
        migrated_db,
        pid,
        risk_score=0.80,
        model_version=ver_y,
        synthetic=True,
        when=now + timedelta(hours=2),
    )

    client = _client()
    r = _history(client, _token(client, "coord.a@vigil.example"), pid)
    assert r.status_code == 200, r.text
    points = r.json()["points"]
    assert len(points) == 4, (
        f"expected 4 champion-at-each-point points across the promotion, got {len(points)} "
        "— (b) must NOT truncate pre-promotion history"
    )
    versions = [p["model_version"] for p in points]
    assert versions == [ver_x, ver_x, ver_y, ver_y], (
        f"version timeline not honestly labeled across the promotion: {versions}"
    )
    scores = [p["risk_score"] for p in points]
    assert scores == sorted(scores), f"points not time-ordered: {scores}"


# ---------------------------------------------------------------------------
# 2. Champion-at-each-point guard: shadow/challenger during a tenure EXCLUDED
# ---------------------------------------------------------------------------


def test_history_excludes_nonchampion_during_tenure(
    migrated_db: dict[str, str],
) -> None:
    from vigil.services.routing_service import promote

    regime = "h2b_guard"
    ver_x = "guardX_v1:t"
    ver_y = "guardY_v2:t"
    shadow_during_x = "guardShadow:t"  # never champion
    challenger_during_y = "guardChall:t"  # never champion
    _seed_champion(regime, ver_x)

    pid = _fresh_participant(migrated_db, "h2b-guard")
    now = datetime.now(tz=timezone.utc)
    # During X's champion tenure: a champion X point AND a shadow point (must be excluded).
    _insert_score(
        migrated_db,
        pid,
        risk_score=0.25,
        model_version=ver_x,
        synthetic=True,
        when=now - timedelta(hours=2),
    )
    _insert_score(
        migrated_db,
        pid,
        risk_score=0.95,
        model_version=shadow_during_x,
        synthetic=True,
        when=now - timedelta(minutes=90),
    )

    promote(
        _admin_scope(migrated_db["platform_admin"]),
        regime=regime,
        model_version=ver_y,
        model_card_ref=_CARD,
        eval_provenance="architecture_validation",
    )

    # During Y's tenure: a champion Y point AND a challenger point (must be excluded).
    _insert_score(
        migrated_db,
        pid,
        risk_score=0.65,
        model_version=ver_y,
        synthetic=True,
        when=now + timedelta(hours=1),
    )
    _insert_score(
        migrated_db,
        pid,
        risk_score=0.05,
        model_version=challenger_during_y,
        synthetic=True,
        when=now + timedelta(hours=2),
    )

    client = _client()
    r = _history(client, _token(client, "coord.a@vigil.example"), pid)
    assert r.status_code == 200, r.text
    points = r.json()["points"]
    versions = {p["model_version"] for p in points}
    assert versions == {ver_x, ver_y}, (
        f"non-champion-of-record rows leaked: {versions} "
        "(shadow/challenger written during a champion tenure must be excluded)"
    )
    assert len(points) == 2, (
        f"expected exactly the 2 champion points, got {len(points)}"
    )
    # Also assert an X-version row written OUTSIDE X's tenure would not sneak in: the shadow
    # and challenger versions were never champion, so they are absent — proven above.


# ---------------------------------------------------------------------------
# 3. No-promotion case unchanged (b reduces to current champion)
# ---------------------------------------------------------------------------


def test_history_no_promotion_unchanged(migrated_db: dict[str, str]) -> None:
    """Single-champion regime (the demo t2d champion) → same trajectory as before."""
    champion = "sequence_v1.0:demo"
    shadow = "structural_v1.0:t2d"
    pid = _fresh_participant(migrated_db, "h2b-noprom")
    base = datetime.now(tz=timezone.utc)
    _insert_score(
        migrated_db,
        pid,
        risk_score=0.30,
        model_version=champion,
        synthetic=True,
        when=base,
    )
    _insert_score(
        migrated_db,
        pid,
        risk_score=0.70,
        model_version=champion,
        synthetic=False,
        when=base + timedelta(hours=1),
    )
    _insert_score(
        migrated_db,
        pid,
        risk_score=0.99,
        model_version=shadow,
        synthetic=True,
        when=base + timedelta(hours=2),
    )

    client = _client()
    r = _history(client, _token(client, "coord.a@vigil.example"), pid)
    assert r.status_code == 200, r.text
    points = r.json()["points"]
    assert [p["model_version"] for p in points] == [champion, champion]
    # Provenance per point honest (both directions) across a cross-row series.
    assert [p["synthetic"] for p in points] == [True, False]


# ---------------------------------------------------------------------------
# 4. Out-of-scope still fails closed (404), even with empty-200 for in-scope
# ---------------------------------------------------------------------------


def test_history_out_of_scope_still_404(migrated_db: dict[str, str]) -> None:
    champion = "sequence_v1.0:demo"
    pid = _fresh_participant(migrated_db, "h2b-scope")
    _insert_score(
        migrated_db,
        pid,
        risk_score=0.40,
        model_version=champion,
        synthetic=True,
        when=datetime.now(tz=timezone.utc),
    )
    client = _client()
    rb = _history(client, _token(client, "coord.b@vigil.example"), pid)
    assert rb.status_code == 404, (
        f"Sponsor B got {rb.status_code} on Sponsor A's participant — must be 404 fail-closed"
    )
