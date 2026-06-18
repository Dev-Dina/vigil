"""Gate M1 — the drift PRODUCER → persist → read path (replacing honest-empty).

Proves: the ``compute_drift`` producer computes REAL PSI/KS over the champion score distribution
and persists points; ``GET /monitoring/drift`` returns them (platform/auditor only — sponsor/site
still 403); provenance travels (synthetic cohort); a ``demo_shift`` run is a CONSTRUCTED breach
demonstration (labelled ``constructed_demo`` + breached), never observed drift. The trigger
endpoint is platform_admin-only. PSI/KS MATH correctness is asserted separately in
tests/models/test_drift.py.

Cleans ``drift_metric`` at start + end so the honest-empty assertion elsewhere stays valid under
the session-scoped DB.
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from sqlalchemy import text

from vigil.api.app import create_app
from vigil.db.models import ParticipantScore
from vigil.repositories.session import platform_session, sponsor_bootstrap_session
from vigil.services import drift_service

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


def _band(score: float) -> str:
    return "high" if score > 0.6 else "medium" if score > 0.3 else "low"


def _clear_drift() -> None:
    with platform_session() as session:
        session.execute(text("DELETE FROM drift_metric"))


def _seed_old_champion_scores(ids: dict[str, str], *, n: int = 40) -> None:
    """Seed n OLD champion scores (spread 0..0.5) so they form the reference window."""
    base = datetime.now(tz=timezone.utc) - timedelta(days=2)
    with sponsor_bootstrap_session(ids["sponsor_a"]) as session:
        for i in range(n):
            score = round((i % 10) / 10 * 0.5, 4)  # spread across [0.0, 0.45]
            session.add(
                ParticipantScore(
                    participant_id=uuid.UUID(ids["participant_a"]),
                    sponsor_id=uuid.UUID(ids["sponsor_a"]),
                    trial_id=uuid.UUID(ids["trial_a"]),
                    site_id=uuid.UUID(ids["site_a"]),
                    risk_score=score,
                    risk_band=_band(score),
                    top_factors=[],
                    reasons=[],
                    model_version=_CHAMPION_MV,
                    model_card_ref=_CARD,
                    synthetic=True,
                    computed_at=base + timedelta(minutes=i),
                )
            )
        session.flush()


def test_drift_producer_persists_real_points_and_is_role_gated(
    migrated_db: dict[str, str],
) -> None:
    ids = migrated_db
    _clear_drift()
    _seed_old_champion_scores(ids)
    client = _client()

    # 1. REAL run (no shift): computes PSI + KS, persists 2 points, NOT a constructed demo.
    summary = drift_service.run_drift_detection(regime="t2d", demo_shift=0.0)
    assert summary.status == "computed", summary
    assert summary.points_written == 2  # one psi + one ks
    assert summary.constructed_demo is False
    assert summary.reference_n >= 5 and summary.current_n >= 5

    # 2. /monitoring/drift returns the real points (auditor — platform read).
    drift = client.get(
        "/api/v1/monitoring/drift", headers=_h(_token(client, "auditor@vigil.example"))
    )
    assert drift.status_code == 200, drift.text
    items = drift.json()["items"]
    assert len(items) >= 2
    metrics = {it["metric"] for it in items}
    assert {"psi", "ks"} <= metrics, "both PSI and KS must be surfaced"
    for it in items:
        assert it["synthetic"] is True, "synthetic cohort provenance must travel"
        assert it["constructed_demo"] is False
        assert it["distribution"] == "champion_risk_score"
        assert isinstance(it["value"], float) and isinstance(it["threshold"], float)

    # 3. Role gate: sponsor/site roles are denied the drift surface (the existing guard holds).
    for email in ("coord.a@vigil.example", "pi.a@vigil.example"):
        rd = client.get("/api/v1/monitoring/drift", headers=_h(_token(client, email)))
        assert rd.status_code == 403, f"{email} must be denied /drift"

    _clear_drift()


def test_drift_constructed_breach_demo_is_labelled(migrated_db: dict[str, str]) -> None:
    ids = migrated_db
    _clear_drift()
    _seed_old_champion_scores(ids)
    client = _client()

    # A demo_shift run manufactures a known drift to DEMONSTRATE the detector firing.
    summary = drift_service.run_drift_detection(regime="t2d", demo_shift=0.4)
    assert summary.status == "computed"
    assert summary.constructed_demo is True
    assert summary.breached is True, "a +0.40 shift must breach (PSI and/or KS)"

    drift = client.get(
        "/api/v1/monitoring/drift", headers=_h(_token(client, "admin@vigil.example"))
    )
    assert drift.status_code == 200, drift.text
    items = drift.json()["items"]
    assert items, "demo run must surface points"
    assert any(it["breached"] for it in items), "the breach must be visible"
    for it in items:
        assert it["constructed_demo"] is True, (
            "demo points must be labelled constructed"
        )
        assert "shift demo" in it["distribution"]
        assert "CONSTRUCTED" in it["note"]

    _clear_drift()


def test_drift_run_trigger_is_platform_admin_only(migrated_db: dict[str, str]) -> None:
    client = _client()

    # platform_admin → 202 + job id (enqueued).
    r = client.post(
        "/api/v1/monitoring/drift/run",
        headers=_h(_token(client, "admin@vigil.example")),
        json={"regime": "t2d", "demo_shift": 0.0},
    )
    assert r.status_code == 202, r.text
    assert r.json()["job_id"]

    # auditor (read-only) + sponsor/site → 403 (triggering a job is an action, not a read).
    for email in (
        "auditor@vigil.example",
        "coord.a@vigil.example",
        "oversight.a@vigil.example",
    ):
        rd = client.post(
            "/api/v1/monitoring/drift/run",
            headers=_h(_token(client, email)),
            json={"regime": "t2d"},
        )
        assert rd.status_code == 403, f"{email} must be denied the drift trigger"

    _clear_drift()
