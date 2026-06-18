"""SEC-1 — cross-site-within-tenant narrowing on the DIRECT read APIs (cohort + participant).

The gap this closes: sponsor RLS keys on sponsor_id only, so before SEC-1 a site-scoped
coordinator/CRA saw the WHOLE sponsor's cohort/participants via /cohort and /participants/*.
domain.md scopes site roles to their own site (coordinator/PI) / assigned sites (CRA). These
tests fail without the service-layer scope.permits narrowing and pass with it; broader roles
(sponsor-oversight) are NOT over-narrowed.

Real Postgres via migrated_db; HTTP-level (the real endpoints).
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


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _other_trial_site_participant(ids: dict[str, str]) -> str:
    """A champion-scored participant under a NEW trial+site of Sponsor A (out of coord.a's
    (trial_a, site_a) scope; new trial → no pollution of trial_a's participant count)."""
    from vigil.db.models import Participant, Site, Trial
    from vigil.repositories import scoring as scoring_repo
    from vigil.repositories.session import sponsor_bootstrap_session

    sponsor_a = uuid.UUID(ids["sponsor_a"])
    with sponsor_bootstrap_session(ids["sponsor_a"]) as session:
        t = Trial(sponsor_id=sponsor_a, name="SEC-1 probe trial")
        session.add(t)
        session.flush()
        s = Site(sponsor_id=sponsor_a, trial_id=t.id, name="SEC-1 probe site")
        session.add(s)
        session.flush()
        p = Participant(
            sponsor_id=sponsor_a,
            trial_id=t.id,
            site_id=s.id,
            coded_ref="PT-SEC1-S2",
            status="active",
            risk_score=0.9,
            risk_band="high",
        )
        session.add(p)
        session.flush()
        pid, tid, sid = p.id, t.id, s.id
        scoring_repo.append_score(
            session,
            participant_id=pid,
            sponsor_id=sponsor_a,
            trial_id=tid,
            site_id=sid,
            risk_score=0.9,
            risk_band="high",
            top_factors=["x"],
            reasons=[],
            model_version=_CHAMPION_MV,
            model_card_ref=_CARD,
            synthetic=True,
        )
        return str(pid)


def _cohort_ids(client: TestClient, token: str) -> set[str]:
    r = client.get("/api/v1/cohort", headers=_h(token))
    assert r.status_code == 200, r.text
    return {row["participant_id"] for row in r.json()["items"]}


# ---------------------------------------------------------------------------
# 1. THE GAP, now closed: site-scoped coordinator's /cohort excludes other-site participants
# ---------------------------------------------------------------------------


def test_cohort_site_scoped_excludes_other_site(migrated_db: dict[str, str]) -> None:
    ids = migrated_db
    other = _other_trial_site_participant(ids)
    client = _client()
    seen = _cohort_ids(client, _token(client, "coord.a@vigil.example"))
    assert ids["participant_a"] in seen, (
        "coordinator must see their OWN site's participant"
    )
    assert other not in seen, (
        "CROSS-SITE LEAK on /cohort: site-scoped coordinator saw another site's participant"
    )


def test_cohort_cra_only_assigned_site(migrated_db: dict[str, str]) -> None:
    ids = migrated_db
    other = _other_trial_site_participant(ids)
    client = _client()
    seen = _cohort_ids(
        client, _token(client, "cra@vigil.example")
    )  # granted site_a only
    assert other not in seen, "CRA saw a participant outside its assigned site"


# ---------------------------------------------------------------------------
# 2. No over-narrowing: sponsor-oversight sees the whole sponsor (all sites)
# ---------------------------------------------------------------------------


def test_cohort_sponsor_oversight_sees_all_sites(migrated_db: dict[str, str]) -> None:
    ids = migrated_db
    other = _other_trial_site_participant(ids)
    client = _client()
    seen = _cohort_ids(client, _token(client, "oversight.a@vigil.example"))
    assert {ids["participant_a"], other} <= seen, (
        "sponsor-oversight must see ALL sites within its sponsor (not over-narrowed)"
    )


# ---------------------------------------------------------------------------
# 3. Participant endpoints: cross-site → 404 on every read; own site still 200
# ---------------------------------------------------------------------------


def test_participant_endpoints_cross_site_blocked(migrated_db: dict[str, str]) -> None:
    ids = migrated_db
    other = _other_trial_site_participant(ids)
    client = _client()
    tok = _token(client, "coord.a@vigil.example")  # site_a / trial_a

    for path in (
        f"/api/v1/participants/{other}",
        f"/api/v1/participants/{other}/risk",
        f"/api/v1/participants/{other}/risk/history",
        f"/api/v1/participants/{other}/interventions",
    ):
        r = client.get(path, headers=_h(tok))
        assert r.status_code == 404, (
            f"cross-site read reachable: {path} → {r.status_code} (expected 404)"
        )

    # POST intervention on an out-of-site participant must also fail closed.
    rp = client.post(
        f"/api/v1/participants/{other}/interventions",
        json={"kind": "call", "note": "x"},
        headers=_h(tok),
    )
    assert rp.status_code == 404, (
        f"cross-site intervention write reachable: {rp.status_code}"
    )

    # Sanity: the coordinator's OWN-site participant is still reachable (no over-narrowing).
    own = client.get(f"/api/v1/participants/{ids['participant_a']}", headers=_h(tok))
    assert own.status_code == 200, f"own-site participant should be visible: {own.text}"
