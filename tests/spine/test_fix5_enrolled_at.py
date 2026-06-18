"""FIX-5 — participant.enrolled_at is the trajectory START (not the seed-run now()).

The detail page's "Days Enrolled" = daysSince(enrolled_at). Before FIX-5 the seed left
enrolled_at at the DB default now(), so it read ~0. The seed now anchors enrolled_at to the
computed enrollment_date (== the visit_index-0 timestamp), so it is months in the past. The
cohort row also carries enrolled_at now (Option A) so the triage/dashboard column is real, not a
hard-coded 0 — wired off the SAME scope-bound participant row (no new read semantics).

Real Postgres via migrated_db; part 2 is HTTP-level (the real endpoint).
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import func, select

from vigil.api.app import create_app
from vigil.db.models import Engagement, Participant
from vigil.repositories.session import sponsor_bootstrap_session


def _client() -> TestClient:
    return TestClient(create_app(), raise_server_exceptions=False)


def _token(client: TestClient, email: str) -> str:
    pw = os.environ.get("VIGIL_SEED_PASSWORD", "vigil-dev-password")
    r = client.post("/api/v1/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# PART 1 — the data fix: enrolled_at is the trajectory start, in the past
# ---------------------------------------------------------------------------


def test_seed_enrolled_at_is_trajectory_start_in_past(
    migrated_db: dict[str, str],
) -> None:
    ids = migrated_db
    pid = uuid.UUID(ids["participant_a"])

    with sponsor_bootstrap_session(ids["sponsor_a"]) as session:
        p = session.get(Participant, pid)
        assert p is not None
        enrolled_at = p.enrolled_at
        created_at = p.created_at
        earliest_visit = session.execute(
            select(func.min(Engagement.visit_timestamp)).where(
                Engagement.participant_id == pid
            )
        ).scalar_one()

    assert earliest_visit is not None, (
        "seed bridge should have copied a visit trajectory"
    )

    # enrollment is at/before the first recorded visit (enrolled_at == visit_index-0 timestamp;
    # <= covers a trajectory whose minimum visit_index is > 0).
    assert enrolled_at <= earliest_visit, (
        "enrolled_at must anchor to the trajectory start, not after the first visit"
    )

    # The bug signature is gone: enrolled_at is no longer the seed-run moment (== created_at);
    # it is strictly in the past by a real margin (the trajectory spans many days).
    assert enrolled_at < created_at, (
        "enrolled_at still equals the seed-run now() (the FIX-5 bug) instead of the trajectory start"
    )
    days_enrolled = (datetime.now(tz=timezone.utc) - enrolled_at).days
    assert days_enrolled >= 1, (
        f"days enrolled is {days_enrolled} — enrolled_at is not in the past (Days Enrolled would read 0)"
    )


# ---------------------------------------------------------------------------
# PART 2 — Option A: the cohort row carries enrolled_at (same scoped row as detail)
# ---------------------------------------------------------------------------


def test_cohort_row_carries_enrolled_at_matching_detail(
    migrated_db: dict[str, str],
) -> None:
    ids = migrated_db
    client = _client()
    tok = _token(client, "coord.a@vigil.example")  # site_a / trial_a

    page = client.get("/api/v1/cohort", headers=_h(tok))
    assert page.status_code == 200, page.text
    rows = page.json()["items"]
    assert rows, "coordinator A should see at least their own-site participant"

    row = next(r for r in rows if r["participant_id"] == ids["participant_a"])
    assert "enrolled_at" in row, (
        "CohortRow must carry enrolled_at (Option A) — not a hard-coded 0"
    )
    # real value, in the past (the triage 'Days Enrolled' is now genuine).
    row_enrolled = datetime.fromisoformat(row["enrolled_at"])
    assert (datetime.now(tz=timezone.utc) - row_enrolled).days >= 1

    # Same scope-bound row the detail endpoint serves → identical enrolled_at (field wired, not faked).
    detail = client.get(f"/api/v1/participants/{ids['participant_a']}", headers=_h(tok))
    assert detail.status_code == 200, detail.text
    assert datetime.fromisoformat(detail.json()["enrolled_at"]) == row_enrolled
