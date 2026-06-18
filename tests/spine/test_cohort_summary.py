"""GET /cohort/summary — scope-bound counts/mean for the caller's cohort (api.md § cohort).

The dashboard + triage pages read this (total, by_band, mean_risk). It must be scope-bound exactly
like /cohort: a site coordinator's summary reflects ONLY their site; cross-site + cross-tenant
participants never count toward it; sponsor-oversight is not over-narrowed. Delta-based assertions
(measure before/after adding probes) so the test is order-independent under the session-scoped DB.

Real Postgres via migrated_db; HTTP-level (the real endpoint).
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


def _summary(client: TestClient, token: str, **params: str) -> dict:
    q = "&".join(f"{k}={v}" for k, v in params.items())
    r = client.get(f"/api/v1/cohort/summary{('?' + q) if q else ''}", headers=_h(token))
    assert r.status_code == 200, r.text
    return r.json()


def _high_participant(
    ids: dict[str, str], *, own_site: bool, sponsor_b: bool = False
) -> str:
    """A high-band champion-scored participant. own_site → coord.a's site_a/trial_a (in scope);
    else a NEW trial+site of sponsor A (cross-site) or sponsor B (cross-tenant)."""
    from vigil.db.models import Participant, Site, Trial
    from vigil.repositories import scoring as scoring_repo
    from vigil.repositories.session import sponsor_bootstrap_session

    sponsor_key = "sponsor_b" if sponsor_b else "sponsor_a"
    sponsor = uuid.UUID(ids[sponsor_key])
    with sponsor_bootstrap_session(ids[sponsor_key]) as session:
        if own_site:
            trial_id = uuid.UUID(ids["trial_a"])
            site_id = uuid.UUID(ids["site_a"])
        else:
            t = Trial(sponsor_id=sponsor, name="summary-probe-trial")
            session.add(t)
            session.flush()
            s = Site(sponsor_id=sponsor, trial_id=t.id, name="summary-probe-site")
            session.add(s)
            session.flush()
            trial_id, site_id = t.id, s.id
        p = Participant(
            sponsor_id=sponsor,
            trial_id=trial_id,
            site_id=site_id,
            coded_ref="PT-SUMMARY",
            status="active",
            risk_score=0.91,
            risk_band="high",
        )
        session.add(p)
        session.flush()
        scoring_repo.append_score(
            session,
            participant_id=p.id,
            sponsor_id=sponsor,
            trial_id=trial_id,
            site_id=site_id,
            risk_score=0.91,
            risk_band="high",
            top_factors=["consecutive_missed"],
            reasons=[],
            model_version=_CHAMPION_MV,
            model_card_ref=_CARD,
            synthetic=True,
        )
        return str(p.id)


# ---------------------------------------------------------------------------
# 1. Shape — total, all three by_band keys, mean_risk in [0,1]
# ---------------------------------------------------------------------------


def test_summary_shape(migrated_db: dict[str, str]) -> None:
    client = _client()
    s = _summary(client, _token(client, "coord.a@vigil.example"))
    assert set(s) == {"total", "by_band", "mean_risk"}
    assert set(s["by_band"]) == {"high", "medium", "low"}
    assert isinstance(s["total"], int) and s["total"] >= 0
    assert 0.0 <= s["mean_risk"] <= 1.0
    # the summary aggregates the SAME scoped slice the list returns (small seeded cohort < page).
    cohort = client.get(
        "/api/v1/cohort", headers=_h(_token(client, "coord.a@vigil.example"))
    ).json()
    assert s["total"] == cohort["total"]


# ---------------------------------------------------------------------------
# 2. Scope-bound: cross-site + cross-tenant participants never count for a coordinator
# ---------------------------------------------------------------------------


def test_summary_scope_bound_excludes_other_site_and_tenant(
    migrated_db: dict[str, str],
) -> None:
    client = _client()
    tok = _token(client, "coord.a@vigil.example")  # site_a / trial_a only

    before = _summary(client, tok)
    own = _high_participant(migrated_db, own_site=True)  # in coord.a's scope
    _other = _high_participant(
        migrated_db, own_site=False
    )  # cross-SITE (sponsor A, new site)
    _btenant = _high_participant(  # cross-TENANT (sponsor B)
        migrated_db, own_site=False, sponsor_b=True
    )
    after = _summary(client, tok)

    # Only the OWN-site high participant counts: total +1, high +1 — the other-site and the
    # sponsor-B participants are invisible to this coordinator's summary.
    assert after["total"] == before["total"] + 1, (
        "coordinator summary counted out-of-scope participants (cross-site/cross-tenant leak)"
    )
    assert after["by_band"]["high"] == before["by_band"]["high"] + 1
    assert own  # created


# ---------------------------------------------------------------------------
# 3. No over-narrowing: sponsor-oversight's summary sees all its sites
# ---------------------------------------------------------------------------


def test_summary_sponsor_oversight_not_over_narrowed(
    migrated_db: dict[str, str],
) -> None:
    client = _client()
    tok = _token(client, "oversight.a@vigil.example")  # all sponsor-A sites

    before = _summary(client, tok)
    _high_participant(migrated_db, own_site=True)  # site_a
    _high_participant(migrated_db, own_site=False)  # a different sponsor-A site
    after = _summary(client, tok)

    # Oversight sees BOTH new sponsor-A participants (not over-narrowed to one site).
    assert after["total"] == before["total"] + 2, (
        "sponsor-oversight summary must cover all its sites (not over-narrowed)"
    )


# ---------------------------------------------------------------------------
# 4. Platform roles forbidden (same guard as /cohort)
# ---------------------------------------------------------------------------


def test_summary_platform_forbidden(migrated_db: dict[str, str]) -> None:
    client = _client()
    for email in ("admin@vigil.example", "auditor@vigil.example"):
        r = client.get("/api/v1/cohort/summary", headers=_h(_token(client, email)))
        assert r.status_code == 403, f"{email} reached cohort summary: {r.status_code}"
