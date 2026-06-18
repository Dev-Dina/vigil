"""Wire-2 — frontend cohort wired to the real scoped GET /cohort.

Exercises the SAME endpoint the watchlist/triage views now call:
- Scope/RLS walk: coordinator-A sees only Sponsor A's rows; Sponsor B cannot see A's
  participants (the cohort analog of the sacred cross-tenant test, on the wired read path).
- Synthetic honesty: CohortRow.synthetic reflects the CHAMPION score's flag (B4), BOTH
  directions, as surfaced through the cohort response the frontend renders.

Real Postgres via migrated_db.
"""

from __future__ import annotations

import os
import uuid

from fastapi.testclient import TestClient

from vigil.api.app import create_app

_CHAMPION_MV = "sequence_v1.1:demo"
_CARD = "data/models/t2d/model_card.md"


def _client() -> TestClient:
    return TestClient(create_app(), raise_server_exceptions=False)


def _token(client: TestClient, email: str) -> str:
    pw = os.environ.get("VIGIL_SEED_PASSWORD", "vigil-dev-password")
    r = client.post("/api/v1/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _cohort(client: TestClient, token: str) -> list[dict]:
    r = client.get("/api/v1/cohort", headers={"Authorization": f"Bearer {token}"})
    assert r.status_code == 200, r.text
    return r.json()["items"]


def _make_participant_with_score(
    ids: dict[str, str], coded_ref: str, *, synthetic: bool
) -> uuid.UUID:
    from vigil.db.models import Participant
    from vigil.repositories import scoring as scoring_repo
    from vigil.repositories.session import sponsor_bootstrap_session

    pid = uuid.uuid4()
    sponsor_id = uuid.UUID(ids["sponsor_a"])
    with sponsor_bootstrap_session(ids["sponsor_a"]) as session:
        session.add(
            Participant(
                id=pid,
                sponsor_id=sponsor_id,
                trial_id=uuid.UUID(ids["trial_a"]),
                site_id=uuid.UUID(ids["site_a"]),
                coded_ref=coded_ref,
            )
        )
        session.flush()
        scoring_repo.append_score(
            session,
            participant_id=pid,
            sponsor_id=sponsor_id,
            trial_id=uuid.UUID(ids["trial_a"]),
            site_id=uuid.UUID(ids["site_a"]),
            risk_score=0.5,
            risk_band="medium",
            top_factors=[],
            reasons=[],
            model_version=_CHAMPION_MV,
            model_card_ref=_CARD,
            synthetic=synthetic,
        )
    return pid


# ---------------------------------------------------------------------------
# 1. Scope / RLS walk on the wired GET /cohort read path
# ---------------------------------------------------------------------------


def test_cohort_scope_walk_cross_tenant(migrated_db: dict[str, str]) -> None:
    ids = migrated_db
    client = _client()

    pid_a = str(uuid.UUID(ids["participant_a"]))
    pid_b = str(uuid.UUID(ids["participant_b"]))

    items_a = _cohort(client, _token(client, "coord.a@vigil.example"))
    ids_a = {r["participant_id"] for r in items_a}
    assert pid_a in ids_a, "coordinator A does not see Sponsor A's own participant"
    assert pid_b not in ids_a, (
        "coordinator A can see Sponsor B's participant via GET /cohort — cross-tenant leak"
    )

    items_b = _cohort(client, _token(client, "coord.b@vigil.example"))
    ids_b = {r["participant_id"] for r in items_b}
    assert pid_b in ids_b, "coordinator B does not see Sponsor B's own participant"
    assert pid_a not in ids_b, (
        "coordinator B can see Sponsor A's participant via GET /cohort — cross-tenant leak"
    )


# ---------------------------------------------------------------------------
# 2. Synthetic honesty surfaced through the cohort response — both directions
# ---------------------------------------------------------------------------


def test_cohort_synthetic_flag_both_directions(migrated_db: dict[str, str]) -> None:
    ids = migrated_db
    client = _client()

    pid_true = str(
        _make_participant_with_score(ids, "wire2-synth-true", synthetic=True)
    )
    pid_false = str(
        _make_participant_with_score(ids, "wire2-synth-false", synthetic=False)
    )

    items = _cohort(client, _token(client, "coord.a@vigil.example"))
    by_id = {r["participant_id"]: r for r in items}

    assert pid_true in by_id, "synthetic=True participant missing from cohort"
    assert by_id[pid_true]["synthetic"] is True

    assert pid_false in by_id, "synthetic=False participant missing from cohort"
    assert by_id[pid_false]["synthetic"] is False, (
        "synthetic=False champion score surfaced as True in /cohort — flag is not data-driven"
    )


# ---------------------------------------------------------------------------
# 3. CohortRow shape matches the frontend types.ts contract
# ---------------------------------------------------------------------------


def test_cohort_row_shape_matches_frontend(migrated_db: dict[str, str]) -> None:
    client = _client()
    items = _cohort(client, _token(client, "coord.a@vigil.example"))
    assert items, "expected at least one cohort row for coordinator A"
    row = items[0]
    for key in (
        "participant_id",
        "trial_id",
        "site_id",
        "risk_score",
        "risk_band",
        "top_factors",
        "updated_at",
        "synthetic",
    ):
        assert key in row, f"CohortRow missing {key!r} — frontend/types.ts drift: {row}"
    assert isinstance(row["top_factors"], list)
    assert isinstance(row["synthetic"], bool)
