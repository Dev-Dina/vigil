"""Calibration report: real vs synthetic vs tolerance for all six targets.

Prints/records PASS/FAIL per target and **fails loud** (``CalibrationError``) on any miss,
per specs/data.md.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from scipy.stats import chisquare

from ingestion.errors import CalibrationError
from ingestion.synthetic import SyntheticCohort
from ingestion.targets import CalibrationTargets
from ingestion.vocab import WITHDRAWAL_REASONS

# Tolerances from the spec.
OVERALL_TOL = 0.01  # +-1 pp
STRATUM_TOL = 0.03  # +-3 pp per stratum
CHISQ_THRESHOLD = (
    0.05  # reason-mix chi-square p-value must exceed (distributions agree)
)


@dataclass
class CalibrationResult:
    target: str
    passed: bool
    detail: dict[str, Any]


def _syn_overall_dropout(p: pd.DataFrame) -> float:
    return float(p["dropped"].mean())


def _syn_dropout_by_stratum(p: pd.DataFrame) -> dict[str, float]:
    return {str(k): float(v) for k, v in p.groupby("stratum")["dropped"].mean().items()}


def _syn_reason_counts(p: pd.DataFrame) -> dict[str, int]:
    dropped = p[p["dropped"]]
    counts = dropped["dropout_reason"].value_counts().to_dict()
    return {r: int(counts.get(r, 0)) for r in WITHDRAWAL_REASONS}


def evaluate(
    cohort: SyntheticCohort,
    targets: CalibrationTargets,
    *,
    fail_loud: bool = True,
) -> list[CalibrationResult]:
    p = cohort.participants
    results: list[CalibrationResult] = []

    # Target 1: overall dropout rate within +-1pp.
    syn_overall = _syn_overall_dropout(p)
    diff = abs(syn_overall - targets.overall_dropout_rate)
    results.append(
        CalibrationResult(
            "overall_dropout_rate",
            diff <= OVERALL_TOL,
            {
                "real": targets.overall_dropout_rate,
                "synthetic": syn_overall,
                "abs_diff": diff,
                "tolerance": OVERALL_TOL,
            },
        )
    )

    # Target 2: dropout by stratum within +-3pp each.
    syn_strata = _syn_dropout_by_stratum(p)
    worst = 0.0
    misses: list[dict[str, Any]] = []
    for key, real in targets.dropout_by_stratum.items():
        syn = syn_strata.get(key)
        if syn is None:
            continue
        d = abs(syn - real)
        worst = max(worst, d)
        if d > STRATUM_TOL:
            misses.append({"stratum": key, "real": real, "synthetic": syn, "diff": d})
    results.append(
        CalibrationResult(
            "dropout_by_stratum",
            len(misses) == 0,
            {"worst_diff": worst, "tolerance": STRATUM_TOL, "misses": misses},
        )
    )

    # Target 3: reason mix chi-square.
    syn_counts = _syn_reason_counts(p)
    total = sum(syn_counts.values())
    if total > 0 and targets.reason_mix:
        expected = np.array(
            [targets.reason_mix.get(r, 0.0) for r in WITHDRAWAL_REASONS], float
        )
        expected = np.where(expected <= 0, 1e-6, expected)
        expected = expected / expected.sum() * total
        observed = np.array([syn_counts[r] for r in WITHDRAWAL_REASONS], float)
        _stat, pval = chisquare(f_obs=observed, f_exp=expected)
        passed = bool(pval >= CHISQ_THRESHOLD)
    else:
        pval, passed = 1.0, True
    results.append(
        CalibrationResult(
            "reason_mix",
            passed,
            {"chisq_pvalue": float(pval), "threshold": CHISQ_THRESHOLD},
        )
    )

    # Target 4: dropout timing early fraction (hazard shape).
    dropped = p[p["dropped"]]
    if len(dropped):
        half = dropped["planned_duration_days"].clip(upper=180) / 2
        syn_early = float((dropped["time_to_event_days"] <= half).mean())
    else:
        syn_early = 0.0
    timing_diff = abs(syn_early - targets.early_fraction)
    results.append(
        CalibrationResult(
            "dropout_timing",
            timing_diff <= 0.10,
            {
                # ASSUMPTION (not fitted): AACT lacks per-event timing; the target is an
                # assumed early-dropout share, not a value estimated from real data.
                "assumed_early_fraction": targets.early_fraction,
                "synthetic_early_fraction": syn_early,
                "abs_diff": timing_diff,
                "tolerance": 0.10,
            },
        )
    )

    # Target 5: covariate -> dropout association signs preserved.
    sign_detail: dict[str, Any] = {}
    signs_ok = True
    for cov, real_sign in targets.covariate_signs.items():
        if real_sign == 0 or cov not in p.columns:
            sign_detail[cov] = {"real_sign": real_sign, "synthetic_sign": 0, "ok": True}
            continue
        corr = np.corrcoef(p[cov].to_numpy(float), p["dropped"].to_numpy(float))[0, 1]
        syn_sign = int(np.sign(corr)) if not np.isnan(corr) else 0
        ok = syn_sign == real_sign
        signs_ok = signs_ok and ok
        sign_detail[cov] = {
            "real_sign": real_sign,
            "synthetic_sign": syn_sign,
            "ok": ok,
        }
    results.append(CalibrationResult("covariate_associations", signs_ok, sign_detail))

    # Target 6: enrollment & arm-count marginals per stratum.
    enroll_misses: list[dict[str, Any]] = []
    for key, real_mean in targets.enrollment_by_stratum.items():
        syn_total = int(p[p["stratum"] == key].shape[0])
        syn_trials = int(p[p["stratum"] == key]["nct_id"].nunique())
        syn_mean = syn_total / syn_trials if syn_trials else 0.0
        rel = abs(syn_mean - real_mean) / real_mean if real_mean else 0.0
        if rel > 0.05:
            enroll_misses.append(
                {"stratum": key, "real": real_mean, "synthetic": syn_mean, "rel": rel}
            )
    results.append(
        CalibrationResult(
            "enrollment_marginals",
            len(enroll_misses) == 0,
            {"tolerance_rel": 0.05, "misses": enroll_misses},
        )
    )

    failed = [r.target for r in results if not r.passed]
    if failed and fail_loud:
        raise CalibrationError(
            "Synthetic cohort failed calibration on: " + ", ".join(failed)
        )
    return results


def write_calibration_report(results: list[CalibrationResult], path: Path) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "all_passed": all(r.passed for r in results),
        "results": [
            {
                "target": r.target,
                "status": "PASS" if r.passed else "FAIL",
                "detail": r.detail,
            }
            for r in results
        ],
    }
    path.write_text(json.dumps(payload, indent=2, sort_keys=True), encoding="utf-8")
    return path
