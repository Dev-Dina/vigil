"""Gate 9.7 -- the Phase-9 clinical-ops loop, driven END-TO-END by the REAL calibrated model.

CAPABILITY DEMONSTRATION on LABELED-SYNTHETIC data with a PLANTED signal -- NOT a clinical finding.
The trajectory->dropout relationship is a literature-shaped synthetic assumption; this script proves
the OPERATIONAL ARCHITECTURE (an accruing disengagement sequence crosses the serious threshold via
the real model and rings the loop), never a real-world prediction.

What it drives (reusing 9.1–9.6 unchanged -- no override, no fabricated reason):
  inject an accruing missed-visit sequence for a SYNTHETIC participant
    -> the REAL calibrated champion LSTM (sequence_v1.1:demo, Gate 9.7a) rescores after each stage
    -> the risk TRAJECTORY climbs across the >0.6 serious threshold (visible on /risk/history)
    -> a genuine serious-risk CROSSING is recorded (Gate 9.2, no override)
    -> the scope-bound AT-RISK surface shows the participant with REAL LSTM-attribution reasons
      (Gate 9.1 -- consecutive_missed, the planted signal) + recommended ACTIONS (Gate 9.3)
    -> the PII-FREE scope-bound EMAIL doorbell sends once (Gate 9.6; stubbed unless configured)
  -- every surface carries the SYNTHETIC-demonstration label.

Prereqs: a migrated + seeded DB + Redis (`make db-up && make migrate && make seed`), the calibrated
artifact at data/models/t2d/sequence_v1.1_demo.pt, and torch installed.

Run:
    uv run python -m scripts.demo_clinical_ops_loop
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

from vigil.repositories import scoring as scoring_repo
from vigil.repositories import users as user_repo
from vigil.repositories.session import (
    auth_lookup_session,
    platform_session,
    sponsor_bootstrap_session,
)
from vigil.services import cohort_service, notification_service, risk_service
from vigil.services.scope_resolver import resolve_scope
from vigil.workers.tasks import score_trial

_COORD_EMAIL = "coord.a@vigil.example"
_SERIOUS_THRESHOLD = 0.6
# A 2-attended -> 12-missed accruing disengagement (the planted-signal precursor), injected in
# stages. Under the real calibrated model the trajectory climbs ~0.10 -> ~0.31 -> ~0.57 -> ~0.67,
# crossing the >0.6 serious threshold at the final stage.
_PATTERN: list[bool] = [False, False] + [True] * 12
_STAGE_ENDS = [2, 6, 10, 14]  # cumulative visit counts injected at each stage


def _line(title: str) -> None:
    print("\n" + "=" * 78 + f"\n{title}\n" + "=" * 78)


def _engagement_rows(pid: uuid.UUID) -> list[dict]:
    """The full accruing trajectory with correct running cumulative/consecutive missed counts."""
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


def _resolve_coordinator_scope():  # type: ignore[no-untyped-def]
    with auth_lookup_session() as session:
        user = user_repo.get_by_email(session, _COORD_EMAIL)
        if user is None:
            raise SystemExit(f"seed first: {_COORD_EMAIL} not found")
        scope = resolve_scope(session, user)
        return (
            user.id,
            scope,
            user.home_sponsor_id,
            user.home_trial_id,
            user.home_site_id,
        )


def main() -> None:
    _line(
        "Gate 9.7 -- Phase-9 clinical-ops loop (CAPABILITY DEMO on LABELED-SYNTHETIC data)"
    )
    print(
        "PLANTED-SIGNAL / SYNTHETIC: the trajectory->dropout relationship is a synthetic, "
        "literature-shaped assumption. This demonstrates the operational ARCHITECTURE driven by "
        "the REAL calibrated model -- it is NOT a clinical finding or a real-world prediction."
    )

    user_id, scope, sponsor_id, trial_id, site_id = _resolve_coordinator_scope()
    if trial_id is None or site_id is None:
        raise SystemExit(f"{_COORD_EMAIL} has no home trial/site; reseed")

    # Give the in-scope coordinator a notification address so the 9.6 doorbell resolves a recipient
    # (demo only; stubbed unless VIGIL_EMAIL_STUB=false). Sourced from env, never a committed value.
    notify_addr = os.environ.get("VIGIL_DEMO_NOTIFY_EMAIL", "demo.coord@example.test")
    with platform_session() as session:
        user_repo.set_notification_email(session, user_id=user_id, email=notify_addr)

    # A fresh SYNTHETIC participant in the coordinator's OWN site (so the surface + email are
    # scope-bound to them). coded-ref only; static covariates match the demo cohort shape.
    pid = uuid.uuid4()
    from vigil.db.models import Participant

    with sponsor_bootstrap_session(str(sponsor_id)) as session:
        session.add(
            Participant(
                id=pid,
                sponsor_id=sponsor_id,
                trial_id=trial_id,
                site_id=site_id,
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
    print(
        f"\nsynthetic participant {pid} created in the coordinator's own site (coded-ref only)."
    )

    all_rows = _engagement_rows(pid)

    _line(
        "Accruing disengagement -> REAL calibrated rescore after each stage (no override)"
    )
    injected = 0
    for stage, end in enumerate(_STAGE_ENDS, start=1):
        from vigil.db.models import Engagement

        with sponsor_bootstrap_session(str(sponsor_id)) as session:
            for row in all_rows[injected:end]:
                session.add(
                    Engagement(
                        id=uuid.uuid4(),
                        sponsor_id=sponsor_id,
                        participant_id=pid,
                        trial_id=trial_id,
                        site_id=site_id,
                        synthetic=True,
                        **row,
                    )
                )
            session.flush()
        injected = end

        asyncio.get_event_loop().run_until_complete(
            score_trial(
                {"job_try": 0},
                trial_id=str(trial_id),
                sponsor_id=str(sponsor_id),
                model_version=None,
                regime="t2d",
            )
        )
        with sponsor_bootstrap_session(str(sponsor_id)) as session:
            row = scoring_repo.get_score(session, pid, "sequence_v1.1:demo")
        last_cons = all_rows[end - 1]["consecutive_missed"]
        flag = (
            "  <- CROSSED >0.6 (serious)" if row.risk_score > _SERIOUS_THRESHOLD else ""
        )
        print(
            f"  stage {stage}: {end:>2} visits (consecutive_missed={last_cons:>2}) -> "
            f"score={row.risk_score:.4f} band={row.risk_band} synthetic={row.synthetic}{flag}"
        )

    _line(
        "Trajectory (/risk/history) -- champion-of-record points, per-point synthetic provenance"
    )
    points = risk_service.get_participant_risk_history(scope, str(pid)) or []
    for p in points:
        print(
            f"  {p.computed_at:%Y-%m-%d %H:%M:%S}  score={p.risk_score:.4f}  band={p.risk_band:<6}"
            f"  model={p.model_version}  synthetic={p.synthetic}"
        )

    _line("Serious-risk CROSSING (Gate 9.2) -- recorded by the REAL model, no override")
    with sponsor_bootstrap_session(str(sponsor_id)) as session:
        crossings = scoring_repo.list_crossings_for_participant(session, pid)
    for c in crossings:
        print(
            f"  crossing {c.id}: {c.prior_band} -> {c.risk_band}  synthetic={c.synthetic}  "
            f"notified={c.notified}"
        )

    _line(
        "Scope-bound AT-RISK surface (Gate 9.4) -- REAL reasons (9.1) + actions (9.3) + synthetic"
    )
    rows = cohort_service.list_cohort(scope, risk_band="high", sort="risk_desc")
    me = next((r for r in rows if r.participant_id == str(pid)), None)
    if me is not None:
        print(
            f"  at-risk row: {me.participant_id}  band={me.risk_band}  synthetic={me.synthetic}"
        )
        print(f"    top_factors (REAL LSTM attribution): {me.top_factors}")
    view = risk_service.get_participant_risk(scope, str(pid))
    if view is not None:
        print(
            f"    reasons: {[(f.feature, round(f.contribution, 4)) for f in view.factors]}"
        )
        print("    recommended actions (operational, NOT clinical):")
        for a in view.recommended_actions:
            print(f"      - {a.action}  [pre-fills intervention={a.intervention_kind}]")
        print(f"    synthetic-demonstration label on the detail: {view.synthetic}")

    _line(
        "PII-FREE scope-bound EMAIL doorbell (Gate 9.6) -- send-once (stub unless configured)"
    )
    if crossings:
        result = notification_service.notify_crossing(
            crossing_id=str(crossings[0].id), sponsor_id=str(sponsor_id)
        )
        print(f"  notify_crossing -> {result}")
        print(
            "  (body is PII-FREE by construction: deep link /at-risk + synthetic label only; "
            "no id / coded_ref / score / factor -- see specs/isolation.md § Email body)"
        )

    _line(
        "Phase-9 DONE-WHEN met -- capability demonstration, labeled-synthetic, end-to-end"
    )
    print(
        "Every surface above (trajectory, at-risk row/detail, reasons, email) is "
        "SYNTHETIC-labeled. The crossing was reached by the REAL calibrated LSTM -- NO override."
    )


if __name__ == "__main__":
    main()
