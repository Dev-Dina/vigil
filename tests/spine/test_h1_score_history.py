"""H1 — participant_score APPENDS timestamped history (not upsert-over-current).

Covers specs/scoring.md § Writeback (append history) + § Champion-only + latest surfacing,
and the critical B2c regression: with history + a shadow row, clinical reads return the
NEWEST CHAMPION row — never an older champion row, never a shadow row.

Real Postgres via migrated_db. Fresh participants + explicit computed_at where ordering matters.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient

from vigil.api.app import create_app

_CHAMPION_MV = "sequence_v1.1:demo"
_SHADOW_MV = "structural_v1.0:t2d"
_CARD = "data/models/t2d/model_card.md"


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
    synthetic: bool = True,
    top_factors: list[str] | None = None,
    when: datetime | None = None,
) -> None:
    """Insert a participant_score row with an explicit computed_at (deterministic order)."""
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
            top_factors=top_factors or [],
            reasons=[],
            model_version=model_version,
            model_card_ref=_CARD,
            synthetic=synthetic,
        )
        if when is not None:
            row.computed_at = when
        session.add(row)
        session.flush()


def _token(client: TestClient, email: str) -> str:
    pw = os.environ.get("VIGIL_SEED_PASSWORD", "vigil-dev-password")
    r = client.post("/api/v1/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


# ---------------------------------------------------------------------------
# 1. Append, not overwrite — two runs leave TWO rows (history preserved)
# ---------------------------------------------------------------------------


def test_append_not_overwrite(migrated_db: dict[str, str]) -> None:
    from vigil.repositories import scoring as scoring_repo
    from vigil.repositories.session import sponsor_bootstrap_session

    pid = _fresh_participant(migrated_db, "h1-append")

    def _run(score: float) -> None:
        with sponsor_bootstrap_session(migrated_db["sponsor_a"]) as session:
            scoring_repo.append_score(
                session,
                participant_id=pid,
                sponsor_id=uuid.UUID(migrated_db["sponsor_a"]),
                trial_id=uuid.UUID(migrated_db["trial_a"]),
                site_id=uuid.UUID(migrated_db["site_a"]),
                risk_score=score,
                risk_band="low",
                top_factors=[],
                reasons=[],
                model_version=_CHAMPION_MV,
                model_card_ref=_CARD,
                synthetic=True,
            )

    _run(0.20)
    _run(0.60)  # second run does NOT overwrite the first

    with sponsor_bootstrap_session(migrated_db["sponsor_a"]) as session:
        rows = scoring_repo.list_scores_for_participant(session, pid)
    champ_rows = [r for r in rows if r.model_version == _CHAMPION_MV]
    assert len(champ_rows) == 2, (
        f"expected 2 appended champion history rows, got {len(champ_rows)} "
        "(append regressed to upsert-overwrite)"
    )


# ---------------------------------------------------------------------------
# 2. Newest champion surfaces (repo + HTTP /risk)
# ---------------------------------------------------------------------------


def test_newest_champion_surfaces(migrated_db: dict[str, str]) -> None:
    from vigil.repositories import routing as routing_repo
    from vigil.repositories import scoring as scoring_repo
    from vigil.repositories.session import platform_session, sponsor_bootstrap_session

    pid = _fresh_participant(migrated_db, "h1-newest")
    base = datetime.now(tz=timezone.utc)
    _insert_score(
        migrated_db, pid, risk_score=0.30, model_version=_CHAMPION_MV, when=base
    )
    _insert_score(
        migrated_db,
        pid,
        risk_score=0.80,
        model_version=_CHAMPION_MV,
        when=base + timedelta(hours=1),
    )

    with platform_session() as session:
        champ_versions = routing_repo.champion_model_versions(session)
    with sponsor_bootstrap_session(migrated_db["sponsor_a"]) as session:
        surf = scoring_repo.get_surfaceable_score(
            session, pid, champion_versions=champ_versions
        )
    assert surf is not None
    assert abs(surf.risk_score - 0.80) < 1e-6, (
        f"surfaced {surf.risk_score} — expected newest champion 0.80, not the older 0.30"
    )

    # HTTP /risk surfaces the newest champion score too.
    client = TestClient(create_app(), raise_server_exceptions=False)
    token = _token(client, "coord.a@vigil.example")
    r = client.get(
        f"/api/v1/participants/{pid}/risk",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    assert abs(r.json()["risk_score"] - 0.80) < 1e-6, (
        f"/risk returned {r.json()['risk_score']} — expected newest champion 0.80"
    )


# ---------------------------------------------------------------------------
# 3. B2c champion-only PRESERVED with history + shadow (critical regression)
# ---------------------------------------------------------------------------


def test_champion_only_preserved_with_history_and_shadow(
    migrated_db: dict[str, str],
) -> None:
    pid = _fresh_participant(migrated_db, "h1-champ-only-history")
    base = datetime.now(tz=timezone.utc)
    # Champion history: old 0.30, new 0.80.
    _insert_score(
        migrated_db, pid, risk_score=0.30, model_version=_CHAMPION_MV, when=base
    )
    _insert_score(
        migrated_db,
        pid,
        risk_score=0.80,
        model_version=_CHAMPION_MV,
        when=base + timedelta(hours=1),
    )
    # Shadow with a LATER timestamp and a more extreme score — must never surface.
    _insert_score(
        migrated_db,
        pid,
        risk_score=0.99,
        model_version=_SHADOW_MV,
        when=base + timedelta(hours=2),
    )

    client = TestClient(create_app(), raise_server_exceptions=False)
    token = _token(client, "coord.a@vigil.example")
    r = client.get(
        f"/api/v1/participants/{pid}/risk",
        headers={"Authorization": f"Bearer {token}"},
    )
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["model_version"] == _CHAMPION_MV, (
        f"/risk model_version {body['model_version']!r} — newer shadow row punched through"
    )
    assert abs(body["risk_score"] - 0.80) < 1e-6, (
        f"/risk risk_score {body['risk_score']} — expected newest CHAMPION 0.80, "
        "not older champion 0.30 and not shadow 0.99"
    )


# ---------------------------------------------------------------------------
# 4. Tenant isolation holds on the now-multiple history rows
# ---------------------------------------------------------------------------


def test_history_rows_tenant_isolated(migrated_db: dict[str, str]) -> None:
    from vigil.repositories import scoring as scoring_repo
    from vigil.repositories import users as user_repo
    from vigil.repositories.session import auth_lookup_session, scoped_session
    from vigil.services.scope_resolver import resolve_scope

    pid = _fresh_participant(migrated_db, "h1-isolation")
    base = datetime.now(tz=timezone.utc)
    _insert_score(
        migrated_db, pid, risk_score=0.40, model_version=_CHAMPION_MV, when=base
    )
    _insert_score(
        migrated_db,
        pid,
        risk_score=0.70,
        model_version=_CHAMPION_MV,
        when=base + timedelta(hours=1),
    )

    with auth_lookup_session() as session:
        user_b = user_repo.get_by_email(session, "coord.b@vigil.example")
    assert user_b is not None
    with auth_lookup_session() as session:
        scope_b = resolve_scope(session, user_b)
    with scoped_session(scope_b) as session:
        rows_b = scoring_repo.list_scores_for_participant(session, pid)
    assert rows_b == [], (
        f"Sponsor B sees {len(rows_b)} of Sponsor A's history rows — RLS leak on history"
    )


# ---------------------------------------------------------------------------
# 5. Idempotency change is honest: re-run appends (no error, no overwrite)
# ---------------------------------------------------------------------------


def test_rerun_appends_no_error(migrated_db: dict[str, str]) -> None:
    from vigil.repositories import scoring as scoring_repo
    from vigil.repositories.session import sponsor_bootstrap_session

    pid = _fresh_participant(migrated_db, "h1-rerun")
    for score in (0.10, 0.10, 0.10):  # identical re-runs must not overwrite or raise
        with sponsor_bootstrap_session(migrated_db["sponsor_a"]) as session:
            scoring_repo.append_score(
                session,
                participant_id=pid,
                sponsor_id=uuid.UUID(migrated_db["sponsor_a"]),
                trial_id=uuid.UUID(migrated_db["trial_a"]),
                site_id=uuid.UUID(migrated_db["site_a"]),
                risk_score=score,
                risk_band="low",
                top_factors=[],
                reasons=[],
                model_version=_CHAMPION_MV,
                model_card_ref=_CARD,
                synthetic=True,
            )
    with sponsor_bootstrap_session(migrated_db["sponsor_a"]) as session:
        rows = scoring_repo.list_scores_for_participant(session, pid)
    assert len([r for r in rows if r.model_version == _CHAMPION_MV]) == 3, (
        "identical re-runs did not append 3 history rows"
    )
