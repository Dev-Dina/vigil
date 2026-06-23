"""INTAKE-1 — scoped participant intake (POST /participants).

The point of this suite is the SCOPE-SAFETY PROOF: creation is RLS/scope-enforced exactly like
every other write, so a caller can create ONLY within their own scope and cross-tenant creation is
impossible. The auto-score enqueue is asserted deterministically (the enqueue is monkeypatched —
no Redis, no model run; the worker score path is the existing tested one).

Real Postgres via ``migrated_db`` (RLS is a database feature, not mockable).
"""

from __future__ import annotations

import os
import uuid

from fastapi.testclient import TestClient

from vigil.api.app import create_app

_TRIAL_A_REF = "INTAKE-A-1"


def _client() -> TestClient:
    return TestClient(create_app(), raise_server_exceptions=False)


def _token(client: TestClient, email: str) -> str:
    pw = os.environ.get("VIGIL_SEED_PASSWORD", "vigil-dev-password")
    r = client.post("/api/v1/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _auth(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _patch_enqueue(monkeypatch) -> list[tuple]:  # type: ignore[no-untyped-def]
    """Capture the auto-score enqueue without Redis. Returns the recorded calls list."""
    calls: list[tuple] = []

    async def _fake_enqueue(function: str, *args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append((function, args, kwargs))
        return "job-intake-fake"

    monkeypatch.setattr("vigil.services.scoring_service.enqueue", _fake_enqueue)
    return calls


def _body(ids: dict[str, str], *, coded_ref: str, **over) -> dict:
    base = {
        "coded_ref": coded_ref,
        "trial_id": ids["trial_a"],
        "site_id": ids["site_a"],
        "age_years": 61.0,
        "age_imputed": False,
        "hba1c_pct": 7.4,
        "hba1c_imputed": True,
        "sex": "Female",
        "arm_type": "Active Comparator",
        "visits": [
            {"attended": True},
            {"attended": False},
            {"attended": False},
            {"attended": False},
        ],
    }
    base.update(over)
    return base


# ---------------------------------------------------------------------------
# 1. Happy path — a coordinator creates in THEIR OWN trial/site → 201 + enqueue
# ---------------------------------------------------------------------------


def test_intake_creates_in_own_scope_and_enqueues(
    migrated_db: dict[str, str], monkeypatch
) -> None:
    ids = migrated_db
    calls = _patch_enqueue(monkeypatch)
    client = _client()
    token = _token(client, "coord.a@vigil.example")

    r = client.post(
        "/api/v1/participants",
        json=_body(ids, coded_ref=_TRIAL_A_REF),
        headers=_auth(token),
    )
    assert r.status_code == 201, r.text
    out = r.json()
    pid = out["participant_id"]
    assert out["coded_ref"] == _TRIAL_A_REF
    assert out["trial_id"] == ids["trial_a"] and out["site_id"] == ids["site_a"]
    assert out["synthetic"] is True
    assert out["n_visits"] == 4
    assert out["scoring_job_id"] == "job-intake-fake"

    # The auto-score enqueue happened: a champion score_trial for THIS trial + the server-derived
    # sponsor (champion model_version is None → resolved at job time, never hardcoded here).
    score_calls = [c for c in calls if c[0] == "score_trial"]
    assert len(score_calls) == 1, (
        f"expected exactly one score_trial enqueue, got {calls}"
    )
    _fn, args, kwargs = score_calls[0]
    assert args[0] == ids["trial_a"], "scored the wrong trial"
    assert args[1] is None, "model_version must be None → champion resolved at job time"
    assert kwargs["sponsor_id"] == ids["sponsor_a"], (
        "sponsor must be server-derived (A)"
    )

    # Persisted + scope-readable by the creator (coordinator is a site role → identity surfaces).
    detail = client.get(f"/api/v1/participants/{pid}", headers=_auth(token))
    assert detail.status_code == 200, detail.text
    assert detail.json()["identity"]["coded_ref"] == _TRIAL_A_REF

    # The row + its engagement are persisted under SPONSOR A with synthetic=True throughout.
    from sqlalchemy import select

    from vigil.db.models import Engagement, Participant
    from vigil.repositories.session import sponsor_bootstrap_session

    with sponsor_bootstrap_session(ids["sponsor_a"]) as session:
        p = session.get(Participant, uuid.UUID(pid))
        assert p is not None
        assert str(p.sponsor_id) == ids["sponsor_a"], (
            "sponsor must be server-derived, not client"
        )
        assert str(p.trial_id) == ids["trial_a"] and str(p.site_id) == ids["site_a"]
        assert p.hba1c_pct == 7.4 and p.hba1c_pct_baseline_imputed is True
        assert p.age_years == 61.0 and p.age_years_baseline_imputed is False
        eng = list(
            session.execute(
                select(Engagement).where(Engagement.participant_id == uuid.UUID(pid))
            ).scalars()
        )
        assert len(eng) == 4, "all four intake visits must persist"
        assert all(e.synthetic for e in eng), (
            "every intake engagement row must be synthetic=True"
        )

    # Audit row written (nothing changes silently).
    from vigil.db.models import AuditLog
    from vigil.repositories.session import sponsor_bootstrap_session as _bs

    with _bs(ids["sponsor_a"]) as session:
        audits = (
            session.execute(
                select(AuditLog).where(
                    AuditLog.action == "participant_created",
                    AuditLog.target_id == pid,
                )
            )
            .scalars()
            .all()
        )
    assert audits, "no audit_log row for the created participant"


# ---------------------------------------------------------------------------
# 2. THE CRITICAL TEST — cross-tenant create is impossible
# ---------------------------------------------------------------------------


def test_intake_cross_tenant_create_blocked(
    migrated_db: dict[str, str], monkeypatch
) -> None:
    """Sponsor B's coordinator targets Sponsor A's trial/site → 403, NOTHING persisted, NO enqueue."""
    ids = migrated_db
    calls = _patch_enqueue(monkeypatch)
    client = _client()
    token_b = _token(client, "coord.b@vigil.example")

    ref = "INTAKE-CROSS-TENANT"
    r = client.post(
        "/api/v1/participants",
        json=_body(ids, coded_ref=ref),  # trial_a / site_a — Sponsor A's, NOT coord.b's
        headers=_auth(token_b),
    )
    assert r.status_code == 403, (
        f"cross-tenant create must be 403 scope_denied, got {r.status_code}: {r.text}"
    )

    # Nothing was enqueued (the create was rejected before any side effect).
    assert not calls, f"a denied create must not enqueue scoring: {calls}"

    # Nothing persisted anywhere — neither under Sponsor A nor Sponsor B.
    from vigil.repositories import tenancy as tenancy_repo
    from vigil.repositories.session import sponsor_bootstrap_session

    for sponsor_key in ("sponsor_a", "sponsor_b"):
        with sponsor_bootstrap_session(ids[sponsor_key]) as session:
            rows = tenancy_repo.get_participants_by_coded_ref(session, ref)
            assert rows == [], (
                f"cross-tenant create leaked a row under {sponsor_key}: {rows}"
            )


# ---------------------------------------------------------------------------
# 3. Cross-SITE within the same tenant (SEC-1) — a coordinator cannot reach another site
# ---------------------------------------------------------------------------


def test_intake_cross_site_within_tenant_blocked(
    migrated_db: dict[str, str], monkeypatch
) -> None:
    """coord.a (site_a) targeting Sponsor A's OTHER site (site_a2) → 403; the SEC-1 axis holds."""
    ids = migrated_db
    calls = _patch_enqueue(monkeypatch)
    client = _client()
    token = _token(client, "coord.a@vigil.example")

    ref = "INTAKE-CROSS-SITE"
    r = client.post(
        "/api/v1/participants",
        json=_body(
            ids, coded_ref=ref, trial_id=ids["trial_a2"], site_id=ids["site_a2"]
        ),
        headers=_auth(token),
    )
    assert r.status_code == 403, (
        f"cross-site create must be 403 (SEC-1), got {r.status_code}: {r.text}"
    )
    assert not calls, "a denied create must not enqueue scoring"

    from vigil.repositories import tenancy as tenancy_repo
    from vigil.repositories.session import sponsor_bootstrap_session

    with sponsor_bootstrap_session(ids["sponsor_a"]) as session:
        assert tenancy_repo.get_participants_by_coded_ref(session, ref) == []


# ---------------------------------------------------------------------------
# 4. Role gate — a non-site role may not create, even within its own sponsor
# ---------------------------------------------------------------------------


def test_intake_non_site_roles_forbidden(
    migrated_db: dict[str, str], monkeypatch
) -> None:
    ids = migrated_db
    calls = _patch_enqueue(monkeypatch)
    client = _client()

    # oversight.a IS in Sponsor A (the requested trial is in-scope) but is NOT a site role → 403
    # (proves the role gate is independent of the data-scope check). auditor is platform → 403.
    for email in ("oversight.a@vigil.example", "auditor@vigil.example"):
        token = _token(client, email)
        r = client.post(
            "/api/v1/participants",
            json=_body(ids, coded_ref=f"INTAKE-ROLE-{email[:4]}"),
            headers=_auth(token),
        )
        assert r.status_code == 403, (
            f"{email} (non-site role) must be 403, got {r.status_code}: {r.text}"
        )
    assert not calls, "no role-rejected create may enqueue scoring"


# ---------------------------------------------------------------------------
# 5. Malformed body → 422 (not 500)
# ---------------------------------------------------------------------------


def test_intake_malformed_body_422(migrated_db: dict[str, str]) -> None:
    ids = migrated_db
    client = _client()
    token = _token(client, "coord.a@vigil.example")

    # Missing required fields (coded_ref, site_id).
    r1 = client.post(
        "/api/v1/participants",
        json={"trial_id": ids["trial_a"]},
        headers=_auth(token),
    )
    assert r1.status_code == 422, r1.text

    # Wrong covariate type.
    r2 = client.post(
        "/api/v1/participants",
        json=_body(ids, coded_ref="INTAKE-BADTYPE", age_years="not-a-number"),
        headers=_auth(token),
    )
    assert r2.status_code == 422, r2.text

    # Non-UUID trial_id (caught in the service → 422, never a 500).
    r3 = client.post(
        "/api/v1/participants",
        json=_body(ids, coded_ref="INTAKE-BADUUID", trial_id="not-a-uuid"),
        headers=_auth(token),
    )
    assert r3.status_code == 422, r3.text
