"""Gate 9.7 — the Phase-9 clinical-ops loop done-when, driven by the REAL calibrated model.

End-to-end (the done-when, slow/torch): an accruing missed-visit sequence injected for a SYNTHETIC
participant is rescored by the REAL calibrated champion LSTM (sequence_v1.1:demo, Gate 9.7a) — NO
override — so the risk TRAJECTORY shifts across the >0.6 serious threshold, recording a genuine
CROSSING (9.2); the scope-bound AT-RISK surface (9.4) shows the participant with REAL LSTM
attribution reasons (9.1) + recommended ACTIONS (9.3); the PII-FREE scope-bound EMAIL (9.6, stub)
sends ONCE. Every surface is SYNTHETIC-labeled end-to-end. Plus: the demo seed no longer writes
fabricated reasons (the carried item closed).

CAPABILITY DEMONSTRATION on labeled-synthetic data with a PLANTED signal — NOT a clinical finding.

Marked slow (torch). Requires: live Postgres, data/models/t2d/sequence_v1.1_demo.pt.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

pytestmark = pytest.mark.slow

_CHAMPION_MV = "sequence_v1.1:demo"
_SERIOUS_THRESHOLD = 0.6
# 2 attended → 12 missed: a planted-signal disengagement precursor, injected in accruing stages.
# Trajectory under the real calibrated model: ~0.10 (low) → ~0.31 → ~0.57 (medium) → ~0.67 (high).
_PATTERN = [False, False] + [True] * 12
_STAGE_ENDS = [2, 6, 10, 14]


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


class _CapturingSender:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []

    def send(self, *, to: list[str], subject: str, body: str) -> None:
        self.sent.append({"to": list(to), "subject": subject, "body": body})


def _engagement_rows() -> list[dict]:
    base = datetime.now(tz=timezone.utc) - timedelta(days=400)
    rows: list[dict] = []
    cum = cons = 0
    for vi, missed in enumerate(_PATTERN):
        if missed:
            cum += 1
            cons += 1
        else:
            cons = 0
        rows.append(
            {
                "visit_index": vi,
                "visit_timestamp": base + timedelta(days=30 * vi),
                "attended": not missed,
                "missed": missed,
                "cumulative_missed": cum,
                "consecutive_missed": cons,
            }
        )
    return rows


def _make_coordinator(
    *,
    email: str,
    sponsor_id: uuid.UUID,
    trial_id: uuid.UUID,
    site_id: uuid.UUID,
    notif: str,
) -> uuid.UUID:
    from vigil.core.security import hash_password
    from vigil.db.models import User
    from vigil.domain import Role
    from vigil.repositories.session import platform_session

    with platform_session() as session:
        u = User(
            email=email,
            password_hash=hash_password("x"),
            role=str(Role.COORDINATOR),
            home_sponsor_id=sponsor_id,
            home_trial_id=trial_id,
            home_site_id=site_id,
            notification_email=notif,
        )
        session.add(u)
        session.flush()
        return u.id


def _coordinator_scope(user_id: uuid.UUID):  # type: ignore[no-untyped-def]
    from vigil.repositories.session import auth_lookup_session
    from vigil.services.scope_resolver import resolve_scope
    from vigil.db.models import User

    with auth_lookup_session() as session:
        return resolve_scope(session, session.get(User, user_id))


def _score(sponsor_id: uuid.UUID, trial_id: uuid.UUID) -> None:
    from vigil.workers.tasks import score_trial

    asyncio.get_event_loop().run_until_complete(
        score_trial(
            {"job_try": 0},
            trial_id=str(trial_id),
            sponsor_id=str(sponsor_id),
            model_version=None,
            regime="t2d",
        )
    )


# ---------------------------------------------------------------------------
# 1. THE DONE-WHEN: real calibrated model drives the whole loop (no override)
# ---------------------------------------------------------------------------


def test_done_when_real_calibrated_model_drives_full_loop(
    migrated_db: dict[str, str], monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    from vigil.db.models import Engagement, Participant, Site, Trial
    from vigil.repositories import scoring as scoring_repo
    from vigil.repositories.session import sponsor_bootstrap_session
    from vigil.services import cohort_service, notification_service, risk_service
    from models.leakage_check import assert_no_outcome_features
    from models.t2d.synthetic_data import SEQ_NUMERIC
    from vigil.services.recommended_actions import APPROVED_ACTION_TEXTS

    ids = migrated_db
    sponsor_a = uuid.UUID(ids["sponsor_a"])
    trial_id, pid = uuid.uuid4(), uuid.uuid4()
    site1, site2 = uuid.uuid4(), uuid.uuid4()

    with sponsor_bootstrap_session(ids["sponsor_a"]) as session:
        session.add(
            Trial(
                id=trial_id,
                sponsor_id=sponsor_a,
                name="demo-loop-trial",
                n_sites=3,
                planned_duration_days=365,
                phase="PHASE3",
            )
        )
        session.flush()
        session.add(
            Site(id=site1, sponsor_id=sponsor_a, trial_id=trial_id, name="loop-site-1")
        )
        session.add(
            Site(id=site2, sponsor_id=sponsor_a, trial_id=trial_id, name="loop-site-2")
        )
        session.flush()
        session.add(
            Participant(
                id=pid,
                sponsor_id=sponsor_a,
                trial_id=trial_id,
                site_id=site1,
                coded_ref="PT-DEMO-LOOP",
                status="active",
                age_years=65.0,
                hba1c_pct=7.5,
                bmi=28.0,
                sex="Male",
                arm_type="Placebo Comparator",
            )
        )
        session.flush()

    c1 = _make_coordinator(
        email="loop.c1@vigil.example",
        sponsor_id=sponsor_a,
        trial_id=trial_id,
        site_id=site1,
        notif="loop.c1@example.test",
    )
    _make_coordinator(
        email="loop.c2@vigil.example",
        sponsor_id=sponsor_a,
        trial_id=trial_id,
        site_id=site2,
        notif="loop.c2@example.test",
    )

    # --- accruing injection → REAL calibrated rescore after each stage (no override) -----------
    all_rows = _engagement_rows()
    trajectory: list[tuple[float, str, bool]] = []
    injected = 0
    for end in _STAGE_ENDS:
        with sponsor_bootstrap_session(ids["sponsor_a"]) as session:
            for row in all_rows[injected:end]:
                session.add(
                    Engagement(
                        id=uuid.uuid4(),
                        sponsor_id=sponsor_a,
                        participant_id=pid,
                        trial_id=trial_id,
                        site_id=site1,
                        synthetic=True,
                        **row,
                    )
                )
            session.flush()
        injected = end
        _score(sponsor_a, trial_id)
        with sponsor_bootstrap_session(ids["sponsor_a"]) as session:
            r = scoring_repo.get_score(session, pid, _CHAMPION_MV)
        trajectory.append((r.risk_score, r.risk_band, r.synthetic))

    # the TRAJECTORY shifts across the threshold via the real model.
    assert trajectory[0][0] <= _SERIOUS_THRESHOLD, (
        f"adherent first stage already high ({trajectory[0][0]:.4f}); no shift to demonstrate"
    )
    final_score, final_band, final_synth = trajectory[-1]
    assert final_score > _SERIOUS_THRESHOLD and final_band == "high", (
        f"real calibrated model did not cross >0.6 (final {final_score:.4f}/{final_band})"
    )
    assert all(s for _, _, s in trajectory), "every score must be synthetic-labeled"

    # --- a genuine crossing recorded (9.2), synthetic ------------------------------------------
    with sponsor_bootstrap_session(ids["sponsor_a"]) as session:
        crossings = scoring_repo.list_crossings_for_participant(session, pid)
    assert len(crossings) == 1, f"expected exactly one crossing, got {len(crossings)}"
    crossing = crossings[0]
    assert crossing.risk_band == "high" and crossing.synthetic is True

    # --- scope-bound at-risk surface (9.4) with REAL reasons (9.1) + actions (9.3) -------------
    scope_c1 = _coordinator_scope(c1)
    at_risk = cohort_service.list_cohort(scope_c1, risk_band="high", sort="risk_desc")
    row = next((x for x in at_risk if x.participant_id == str(pid)), None)
    assert row is not None, "participant must appear on the scope-bound at-risk surface"
    assert row.synthetic is True, "at-risk row must carry the synthetic label"
    assert row.top_factors, (
        "at-risk row must surface REAL champion attribution reasons (9.1)"
    )
    assert set(row.top_factors) <= set(SEQ_NUMERIC), (
        "reasons must be the model's OWN inputs"
    )

    view = risk_service.get_participant_risk(scope_c1, str(pid))
    assert view is not None and view.factors, "risk detail must carry real reasons"
    names = [f.feature for f in view.factors]
    assert_no_outcome_features(names)  # leakage-safe surface (must not raise)
    assert view.synthetic is True
    assert view.recommended_actions, (
        "high-band participant must get recommended actions (9.3)"
    )
    assert all(a.action in APPROVED_ACTION_TEXTS for a in view.recommended_actions), (
        "only approved operational action texts may surface (no free/clinical text)"
    )

    # --- trajectory endpoint (9.4 sparkline source): per-point synthetic provenance ------------
    points = risk_service.get_participant_risk_history(scope_c1, str(pid))
    assert points and all(p.synthetic for p in points), (
        "history points must be synthetic-labeled"
    )
    assert points[-1].risk_band == "high"

    # --- PII-free scope-bound email doorbell (9.6), send-once ----------------------------------
    sender = _CapturingSender()
    monkeypatch.setattr(notification_service, "get_email_sender", lambda: sender)
    result = notification_service.notify_crossing(
        crossing_id=str(crossing.id), sponsor_id=ids["sponsor_a"]
    )
    assert result["status"] == "sent"
    assert len(sender.sent) == 1, "the doorbell sends exactly once"
    to = sender.sent[0]["to"]
    assert "loop.c1@example.test" in to, (
        "the in-scope site-1 coordinator must be a recipient"
    )
    assert "loop.c2@example.test" not in to, (
        "CROSS-SITE LEAK: a site-2 coordinator must NOT receive a site-1 crossing email"
    )
    body = str(sender.sent[0]["subject"]) + "\n" + str(sender.sent[0]["body"])
    # PII-free by construction: no id / coded_ref / score / factor.
    assert str(pid) not in body and "PT-DEMO-LOOP" not in body
    assert f"{final_score:.2f}" not in body and "consecutive_missed" not in body
    # synthetic label present end-to-end on the email.
    assert "synthetic-data demonstration" in body and "/at-risk" in body

    # send-once guard: a re-fire does not re-send.
    again = notification_service.notify_crossing(
        crossing_id=str(crossing.id), sponsor_id=ids["sponsor_a"]
    )
    assert again["status"] == "already_notified"
    assert len(sender.sent) == 1, "a re-fire must not double-send"


# ---------------------------------------------------------------------------
# 2. Demo-seed cleanup: the seed no longer writes fabricated reasons
# ---------------------------------------------------------------------------


def test_seed_writes_no_fabricated_factors(migrated_db: dict[str, str]) -> None:
    """The seeded demo score (the EARLIEST participant_a row, written at seed time) carries EMPTY
    top_factors/reasons — no hand-authored clinical rationale; real factors come only from a real
    calibrated-champion score (Gate 9.7)."""
    from vigil.repositories import scoring as scoring_repo
    from vigil.repositories.session import sponsor_bootstrap_session

    ids = migrated_db
    with sponsor_bootstrap_session(ids["sponsor_a"]) as session:
        rows = scoring_repo.list_scores_for_participant(
            session, uuid.UUID(ids["participant_a"])
        )
    assert rows, "expected at least the seeded score row for participant_a"
    seed_row = min(
        rows, key=lambda r: r.computed_at
    )  # the seed row precedes any test rescore
    assert seed_row.top_factors == [], (
        f"seed must not write fabricated top_factors; got {seed_row.top_factors}"
    )
    assert seed_row.reasons == [], (
        f"seed must not write fabricated reasons; got {seed_row.reasons}"
    )
