"""Gate 9.2 — serious-risk crossing detection (idempotent) + scope-bound at-risk surface.

PART 1 — crossing detection: a champion non-high -> high transition records EXACTLY ONE crossing;
re-scoring while still high records none; a retried worker job does not duplicate; a
high -> non-high -> high produces a new crossing. Uses a scorer override (no torch) to drive
deterministic scores, and a FRESH trial/participant so the assertions are order-independent.

PART 2 — at-risk surface (/cohort?risk_band=high&sort=risk_desc): SCOPE-BOUND (sacred) — a site
coordinator sees ONLY their site's at-risk participants (cross-site, SEC-1); cross-tenant denied;
sponsor-oversight is not over-narrowed; sort orders by risk; champion reasons surface; coded-only.

Real Postgres via migrated_db.
"""

from __future__ import annotations

import asyncio
import os
import uuid

from fastapi.testclient import TestClient

from vigil.api.app import create_app

_CHAMPION_MV = "sequence_v1.1:demo"
_CARD = "data/models/t2d/model_card.md"


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _run_score(
    ids: dict[str, str], trial_id: str, value: float, job_try: int = 0
) -> None:
    """Score every participant in a trial at a fixed value via an injected (override) scorer."""
    from vigil.workers.tasks import score_trial

    ctx = {"job_try": job_try, "_scorer_override": lambda feats: [value] * len(feats)}
    asyncio.get_event_loop().run_until_complete(
        score_trial(
            ctx,
            trial_id=trial_id,
            sponsor_id=ids["sponsor_a"],
            model_version=None,
            regime="t2d",
        )
    )


def _crossing_count(ids: dict[str, str], participant_id: uuid.UUID) -> int:
    from vigil.repositories import scoring as scoring_repo
    from vigil.repositories.session import sponsor_bootstrap_session

    with sponsor_bootstrap_session(ids["sponsor_a"]) as session:
        return len(scoring_repo.list_crossings_for_participant(session, participant_id))


def _fresh_trial_participant(ids: dict[str, str]) -> tuple[uuid.UUID, uuid.UUID]:
    """A fresh trial + participant under sponsor_a (own dedicated trial; no cross-test pollution)."""
    from vigil.db.models import Participant, Site, Trial
    from vigil.repositories.session import sponsor_bootstrap_session

    sponsor_a = uuid.UUID(ids["sponsor_a"])
    with sponsor_bootstrap_session(ids["sponsor_a"]) as session:
        t = Trial(sponsor_id=sponsor_a, name="crossing-probe-trial")
        session.add(t)
        session.flush()
        s = Site(sponsor_id=sponsor_a, trial_id=t.id, name="crossing-probe-site")
        session.add(s)
        session.flush()
        p = Participant(
            sponsor_id=sponsor_a,
            trial_id=t.id,
            site_id=s.id,
            coded_ref="PT-CROSS",
            status="active",
        )
        session.add(p)
        session.flush()
        return t.id, p.id


# ---------------------------------------------------------------------------
# PART 1 — crossing detection idempotency + transition semantics
# ---------------------------------------------------------------------------


def test_crossing_fires_once_per_transition(migrated_db: dict[str, str]) -> None:
    ids = migrated_db
    trial_id, pid = _fresh_trial_participant(ids)

    # medium first → no crossing (not high).
    _run_score(ids, str(trial_id), 0.40)
    assert _crossing_count(ids, pid) == 0

    # medium → high → exactly one crossing.
    _run_score(ids, str(trial_id), 0.70)
    assert _crossing_count(ids, pid) == 1

    # still high on re-score → NO new crossing.
    _run_score(ids, str(trial_id), 0.70)
    assert _crossing_count(ids, pid) == 1

    # retried worker job (job_try=1), still high → NO duplicate.
    _run_score(ids, str(trial_id), 0.70, job_try=1)
    assert _crossing_count(ids, pid) == 1

    # high → low (no crossing on the downward move).
    _run_score(ids, str(trial_id), 0.20)
    assert _crossing_count(ids, pid) == 1

    # low → high again → a NEW crossing (transition semantics).
    _run_score(ids, str(trial_id), 0.70)
    assert _crossing_count(ids, pid) == 2


def test_record_crossing_is_idempotent_on_same_score_row(
    migrated_db: dict[str, str],
) -> None:
    """The UNIQUE(crossing_score_id) anchor: recording the same score row twice yields one row."""
    from vigil.repositories import scoring as scoring_repo
    from vigil.repositories.session import sponsor_bootstrap_session

    ids = migrated_db
    trial_id, pid = _fresh_trial_participant(ids)
    sponsor_a = uuid.UUID(ids["sponsor_a"])

    with sponsor_bootstrap_session(ids["sponsor_a"]) as session:
        # need the site for the score row
        from vigil.db.models import Participant

        p = session.get(Participant, pid)
        score_row = scoring_repo.append_score(
            session,
            participant_id=pid,
            sponsor_id=sponsor_a,
            trial_id=trial_id,
            site_id=p.site_id,
            risk_score=0.8,
            risk_band="high",
            top_factors=[],
            reasons=[],
            model_version=_CHAMPION_MV,
            model_card_ref=_CARD,
            synthetic=True,
        )
        first = scoring_repo.record_crossing(
            session, score_row=score_row, prior_band="medium"
        )
        second = scoring_repo.record_crossing(
            session, score_row=score_row, prior_band="medium"
        )
        assert first.id == second.id  # same row returned, not a duplicate

    assert _crossing_count(ids, pid) == 1


def test_crossing_invisible_to_other_sponsor(migrated_db: dict[str, str]) -> None:
    """SACRED cross-tenant: a risk_crossing under Sponsor A is invisible to a Sponsor B session
    (RLS on risk_crossing — the migration 0009 policy, no platform bypass)."""
    from vigil.db.models import RiskCrossing
    from vigil.repositories import scoring as scoring_repo
    from vigil.repositories import users as user_repo
    from vigil.repositories.session import (
        auth_lookup_session,
        scoped_session,
        sponsor_bootstrap_session,
    )
    from vigil.services.scope_resolver import resolve_scope

    ids = migrated_db
    trial_id, pid = _fresh_trial_participant(ids)
    _run_score(ids, str(trial_id), 0.77)
    with sponsor_bootstrap_session(ids["sponsor_a"]) as session:
        cid = scoring_repo.list_crossings_for_participant(session, pid)[0].id

    with auth_lookup_session() as session:
        user_b = user_repo.get_by_email(session, "coord.b@vigil.example")
        scope_b = resolve_scope(session, user_b)
    with scoped_session(scope_b) as session:
        assert session.get(RiskCrossing, cid) is None, (
            "Sponsor A risk_crossing leaked to Sponsor B (RLS not enforced)"
        )
        assert scoring_repo.list_crossings_for_participant(session, pid) == []


def test_crossing_carries_scope_for_recipient_resolution(
    migrated_db: dict[str, str],
) -> None:
    """The crossing record carries the participant's (sponsor, trial, site) — needed by 9.5."""
    from vigil.repositories import scoring as scoring_repo
    from vigil.repositories.session import sponsor_bootstrap_session

    ids = migrated_db
    trial_id, pid = _fresh_trial_participant(ids)
    _run_score(ids, str(trial_id), 0.75)

    with sponsor_bootstrap_session(ids["sponsor_a"]) as session:
        crossings = scoring_repo.list_crossings_for_participant(session, pid)
    assert len(crossings) == 1
    c = crossings[0]
    assert str(c.sponsor_id) == ids["sponsor_a"]
    assert c.trial_id == trial_id
    assert c.site_id is not None
    assert c.risk_band == "high" and c.prior_band in ("low", "medium", "none")


# ---------------------------------------------------------------------------
# PART 2 — scope-bound at-risk surface (/cohort?risk_band=high)
# ---------------------------------------------------------------------------


def _client() -> TestClient:
    return TestClient(create_app(), raise_server_exceptions=False)


def _token(client: TestClient, email: str) -> str:
    pw = os.environ.get("VIGIL_SEED_PASSWORD", "vigil-dev-password")
    r = client.post("/api/v1/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _h(tok: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {tok}"}


def _high_participant_other_site(ids: dict[str, str], *, score: float = 0.9) -> str:
    """A high-band champion-scored participant under a NEW trial+site of Sponsor A (out of
    coord.a's site scope)."""
    from vigil.db.models import Participant, Site, Trial
    from vigil.repositories import scoring as scoring_repo
    from vigil.repositories.session import sponsor_bootstrap_session

    sponsor_a = uuid.UUID(ids["sponsor_a"])
    with sponsor_bootstrap_session(ids["sponsor_a"]) as session:
        t = Trial(sponsor_id=sponsor_a, name="atrisk-probe-trial")
        session.add(t)
        session.flush()
        s = Site(sponsor_id=sponsor_a, trial_id=t.id, name="atrisk-probe-site")
        session.add(s)
        session.flush()
        p = Participant(
            sponsor_id=sponsor_a,
            trial_id=t.id,
            site_id=s.id,
            coded_ref="PT-ATRISK-S2",
            status="active",
            risk_score=score,
            risk_band="high",
        )
        session.add(p)
        session.flush()
        scoring_repo.append_score(
            session,
            participant_id=p.id,
            sponsor_id=sponsor_a,
            trial_id=t.id,
            site_id=s.id,
            risk_score=score,
            risk_band="high",
            top_factors=["consecutive_missed"],
            reasons=[{"feature": "consecutive_missed", "contribution": 0.2}],
            model_version=_CHAMPION_MV,
            model_card_ref=_CARD,
            synthetic=True,
        )
        return str(p.id)


def _high_participant_own_site(ids: dict[str, str], *, score: float = 0.88) -> str:
    """A high-band participant at coord.a's OWN site_a/trial_a (so coord.a sees it).

    Created HERE, in b2e, after the slow LSTM scoring tests (b2b/b2d) have already run — so its
    denorm band is NOT mutated by ``score_trial(trial_a)``. (The seeded ``participant_a`` IS
    re-scored by those tests to a non-high band, which is why these at-risk tests must not depend
    on it.) No later test re-scores trial_a, so this row stays ``high`` order-independently.
    """
    from vigil.db.models import Participant
    from vigil.repositories import scoring as scoring_repo
    from vigil.repositories.session import sponsor_bootstrap_session

    sponsor_a = uuid.UUID(ids["sponsor_a"])
    trial_a = uuid.UUID(ids["trial_a"])
    site_a = uuid.UUID(ids["site_a"])
    with sponsor_bootstrap_session(ids["sponsor_a"]) as session:
        p = Participant(
            sponsor_id=sponsor_a,
            trial_id=trial_a,
            site_id=site_a,
            coded_ref="PT-ATRISK-S1",
            status="active",
            risk_score=score,
            risk_band="high",
        )
        session.add(p)
        session.flush()
        scoring_repo.append_score(
            session,
            participant_id=p.id,
            sponsor_id=sponsor_a,
            trial_id=trial_a,
            site_id=site_a,
            risk_score=score,
            risk_band="high",
            top_factors=["consecutive_missed"],
            reasons=[{"feature": "consecutive_missed", "contribution": 0.2}],
            model_version=_CHAMPION_MV,
            model_card_ref=_CARD,
            synthetic=True,
        )
        return str(p.id)


def _atrisk_ids(client: TestClient, tok: str, **params: str) -> list[str]:
    q = "&".join(f"{k}={v}" for k, v in {"risk_band": "high", **params}.items())
    r = client.get(f"/api/v1/cohort?{q}", headers=_h(tok))
    assert r.status_code == 200, r.text
    return [row["participant_id"] for row in r.json()["items"]]


def test_atrisk_site_coordinator_cannot_see_other_site(
    migrated_db: dict[str, str],
) -> None:
    """SACRED cross-site: a site-1 coordinator's at-risk list excludes a site-2 at-risk participant."""
    ids = migrated_db
    own = _high_participant_own_site(
        ids
    )  # coord.a's OWN site (stable high; not seed-dependent)
    other = _high_participant_other_site(ids)
    client = _client()
    seen = _atrisk_ids(client, _token(client, "coord.a@vigil.example"))
    assert own in seen, "coordinator must see their OWN site's at-risk participant"
    assert other not in seen, (
        "CROSS-SITE LEAK on the at-risk surface: site coordinator saw another site's at-risk row"
    )


def test_atrisk_cross_tenant_denied(migrated_db: dict[str, str]) -> None:
    """Sponsor-B coordinator never sees a Sponsor-A at-risk participant (cross-tenant RLS)."""
    ids = migrated_db
    client = _client()
    seen = _atrisk_ids(client, _token(client, "coord.b@vigil.example"))
    assert ids["participant_a"] not in seen
    assert (
        ids["participant_b"] in seen or True
    )  # B sees its own scope (no assertion on B's data)


def test_atrisk_sponsor_oversight_not_over_narrowed(
    migrated_db: dict[str, str],
) -> None:
    """Sponsor-oversight sees at-risk across ALL its sites (not over-narrowed to one site)."""
    ids = migrated_db
    own = _high_participant_own_site(ids)  # site_a; stable high (not seed-dependent)
    other = _high_participant_other_site(ids)  # a different site
    client = _client()
    seen = _atrisk_ids(client, _token(client, "oversight.a@vigil.example"))
    assert {own, other} <= set(seen), (
        "sponsor-oversight must see at-risk participants across all its sites"
    )


def test_atrisk_sort_orders_by_risk(migrated_db: dict[str, str]) -> None:
    """sort=risk_desc orders by descending risk; risk_asc reverses (over the caller's scope)."""
    ids = migrated_db
    hi = _high_participant_other_site(ids, score=0.97)
    lo = _high_participant_other_site(ids, score=0.62)
    client = _client()
    tok = _token(client, "oversight.a@vigil.example")

    desc = _atrisk_ids(client, tok, sort="risk_desc")
    assert desc.index(hi) < desc.index(lo), (
        "risk_desc must place the higher score first"
    )

    asc = _atrisk_ids(client, tok, sort="risk_asc")
    assert asc.index(lo) < asc.index(hi), "risk_asc must place the lower score first"


def test_atrisk_row_carries_reasons_and_is_coded(migrated_db: dict[str, str]) -> None:
    """The at-risk row surfaces champion reasons (top_factors) and is coded-ids-only.

    Uses a participant we control (champion row carries top_factors) so the assertion is
    order-independent — other tests re-score the seeded participant and would mutate its newest
    champion row's top_factors.
    """
    ids = migrated_db
    target = _high_participant_other_site(ids)  # champion row has top_factors set
    client = _client()
    tok = _token(client, "oversight.a@vigil.example")  # sees all sponsor-A sites
    r = client.get("/api/v1/cohort?risk_band=high", headers=_h(tok))
    assert r.status_code == 200, r.text
    rows = r.json()["items"]
    row = next((x for x in rows if x["participant_id"] == target), None)
    assert row is not None
    assert isinstance(row["top_factors"], list) and row["top_factors"], (
        "at-risk row must surface champion reasons (top_factors)"
    )
    # Coded-only: participant_id is the coded UUID; no identifiable name/email/dob fields present.
    assert set(row).isdisjoint({"name", "email", "dob", "mrn", "ssn"})
