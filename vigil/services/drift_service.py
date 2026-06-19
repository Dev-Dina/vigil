"""Drift producer (Gate M1) — compute PSI/KS on the champion score distribution + persist.

The PRODUCER behind ``GET /monitoring/drift`` (was honest-empty). It compares a REFERENCE window
(older champion scores) against a CURRENT window (recent champion scores) of the model's pooled
output distribution and writes the resulting PSI/KS points to ``drift_metric``.

Cross-tenant aggregation, security-preserving: ``participant_score`` is sponsor-RLS (no platform
bypass), so the model's output distribution can't be platform-read in one query. This worker-side
job enumerates sponsors (the sponsor table IS platform-readable) and reads each sponsor's champion
scores via ``sponsor_bootstrap_session`` — the SAME trusted infra path scoring already uses — then
pools ONLY the scalar ``risk_score`` values. No tenant row is persisted: the output is a model-level
PSI/KS number in the platform ``drift_metric`` table. Surfacing stays platform/auditor-only.

Honesty: ``synthetic=True`` when the pooled scores came from the synthetic cohort; a breach can be
DEMONSTRATED with ``demo_shift`` (the current window = the reference shifted by a constant), which
sets ``constructed_demo=True`` + a labelling note — a constructed demonstration of the detector, NOT
observed production drift. With ``demo_shift=0`` the numbers are real PSI/KS on the real (unshifted)
windows.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
from sqlalchemy import asc, select

from vigil.core.logging import get_logger
from vigil.db.models import ParticipantScore, Sponsor
from vigil.repositories import drift as drift_repo
from vigil.repositories import routing as routing_repo
from vigil.repositories.session import platform_session, sponsor_bootstrap_session
from models.drift import evaluate_drift

log = get_logger("vigil.drift")

_DISTRIBUTION = "champion_risk_score"
_MIN_PER_WINDOW = 5  # below this a PSI/KS reading is too unstable to report honestly


@dataclass(frozen=True, slots=True)
class DriftRunSummary:
    regime: str
    model_version: str | None
    status: str  # "computed" | "no_champion" | "insufficient_data"
    reference_n: int
    current_n: int
    points_written: int
    breached: bool
    constructed_demo: bool
    # Gate M2: the breached anchor point id to ALERT on (a NEW breach EVENT only — the
    # not-breached → breached EDGE). None when this run is not a fresh breach (no breach, or the
    # breach was already ongoing → no re-alert). The producer (compute_drift) enqueues the alert.
    alert_point_id: str | None = None


def _pooled_champion_scores(
    champion_versions: set[str], sponsor_ids: list[str]
) -> list[tuple[object, float, bool]]:
    """Pool champion scores across tenants as ``(computed_at, risk_score, synthetic)``, time-ordered.

    Each sponsor's rows are read under its OWN RLS-bound session; only the scalar risk_score (+ its
    timestamp + synthetic flag) is pooled — no tenant-identifying data leaves the per-sponsor read.
    The pooled list is sorted by ``computed_at`` so the reference/current split is a genuine
    older-vs-recent window.
    """
    rows: list[tuple[object, float, bool]] = []
    for sid in sponsor_ids:
        with sponsor_bootstrap_session(sid) as session:
            for score, computed_at, synthetic in session.execute(
                select(
                    ParticipantScore.risk_score,
                    ParticipantScore.computed_at,
                    ParticipantScore.synthetic,
                )
                .where(ParticipantScore.model_version.in_(list(champion_versions)))
                .order_by(asc(ParticipantScore.computed_at))
            ).all():
                rows.append((computed_at, float(score), bool(synthetic)))
    rows.sort(key=lambda r: r[0])
    return rows


def run_drift_detection(
    *,
    regime: str = "t2d",
    demo_shift: float = 0.0,
    reference_fraction: float = 0.5,
) -> DriftRunSummary:
    """Compute + persist PSI/KS for the champion score distribution. Returns a summary.

    Splits the time-ordered pooled champion scores into a reference (oldest ``reference_fraction``)
    and a current window. ``demo_shift != 0`` replaces the current window with the reference shifted
    by that constant (clipped to [0,1]) → a CONSTRUCTED breach demonstration (labelled). No points
    are written when there is no champion or too few scores per window (honest — never fabricated).
    """
    with platform_session() as session:
        champion_versions = routing_repo.champion_model_versions(session)
        champion_row = routing_repo.get_champion(session, regime=regime)
        sponsor_ids = [
            str(s) for s in session.execute(select(Sponsor.id)).scalars().all()
        ]

    model_version = champion_row.model_version if champion_row is not None else None
    if not champion_versions or model_version is None:
        log.info("drift.no_champion", extra={"extra": {"regime": regime}})
        return DriftRunSummary(regime, None, "no_champion", 0, 0, 0, False, False)

    rows = _pooled_champion_scores(champion_versions, sponsor_ids)
    scores = np.array([r[1] for r in rows], dtype=float)
    any_synthetic = any(r[2] for r in rows)

    n = scores.size
    split = int(n * reference_fraction)
    reference = scores[:split]
    if demo_shift != 0.0:
        # CONSTRUCTED demonstration: shift the reference to manufacture a known drift.
        current = np.clip(reference + demo_shift, 0.0, 1.0)
        constructed_demo = True
        distribution = f"{_DISTRIBUTION} (constructed {demo_shift:+.2f} shift demo)"
        note = (
            "CONSTRUCTED demonstration: current window = reference shifted by "
            f"{demo_shift:+.2f} (clipped [0,1]) — demonstrates the detector firing, NOT observed "
            "production drift."
        )
    else:
        current = scores[split:]
        constructed_demo = False
        distribution = _DISTRIBUTION
        note = "Real PSI/KS over older-vs-recent champion scores (synthetic cohort)."

    if reference.size < _MIN_PER_WINDOW or current.size < _MIN_PER_WINDOW:
        log.info(
            "drift.insufficient_data",
            extra={
                "extra": {
                    "reference_n": int(reference.size),
                    "current_n": int(current.size),
                }
            },
        )
        return DriftRunSummary(
            regime,
            model_version,
            "insufficient_data",
            int(reference.size),
            int(current.size),
            0,
            False,
            constructed_demo,
        )

    results = evaluate_drift(reference, current)
    any_breached = any(r.breached for r in results)

    written = 0
    alert_point_id: str | None = None
    with platform_session() as session:
        # Gate M2 EDGE: read each metric's PRIOR breach state BEFORE this run's appends, so a
        # transition INTO breach (prior not breached / no prior) can be told apart from an ONGOING
        # breach (prior already breached → no re-alert). Mirrors the crossing non-high→high edge.
        prev_breached = {
            r.metric: drift_repo.latest_breached_state(
                session,
                regime=regime,
                model_version=model_version,
                metric=r.metric,
            )
            for r in results
        }
        for r in results:
            r_note = note
            if r.metric == "ks":
                r_note = f"{note} p_value={r.detail.get('p_value'):.4g} (alpha={r.detail.get('alpha')})."
            row = drift_repo.insert_drift_point(
                session,
                regime=regime,
                model_version=model_version,
                distribution=distribution,
                metric=r.metric,
                value=r.value,
                threshold=r.threshold,
                breached=r.breached,
                reference_n=r.reference_n,
                current_n=r.current_n,
                synthetic=any_synthetic,
                constructed_demo=constructed_demo,
                note=r_note,
            )
            written += 1
            # Alert anchor = the FIRST metric that newly breached this run (prior not breached).
            # One alert per breach EVENT (run-level), even when both PSI and KS breach together.
            if (
                alert_point_id is None
                and r.breached
                and not prev_breached.get(r.metric)
            ):
                alert_point_id = str(row.id)

    log.info(
        "drift.computed",
        extra={
            "extra": {
                "regime": regime,
                "model_version": model_version,
                "points": written,
                "breached": any_breached,
                "constructed_demo": constructed_demo,
                "reference_n": int(reference.size),
                "current_n": int(current.size),
                "alert": alert_point_id is not None,
            }
        },
    )
    return DriftRunSummary(
        regime,
        model_version,
        "computed",
        int(reference.size),
        int(current.size),
        written,
        any_breached,
        constructed_demo,
        alert_point_id,
    )
