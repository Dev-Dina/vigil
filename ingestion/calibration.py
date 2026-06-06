"""Calibration report: REAL vs synthetic vs tolerance for every spec target.

Prints/records PASS/FAIL per target and **fails loud** (``CalibrationError``) on any miss,
per specs/data.md. "Real" values are the captured AACT/EDA targets (``ingestion.targets``);
"synthetic" values are recomputed from the generated cohort. Each per-stratum row shows real,
synthetic, abs-diff, tolerance, PASS/FAIL.
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
TIMING_TOL = 0.10  # early-fraction (assumed) tolerance
ENROLL_REL_TOL = 0.05  # enrollment/arm marginal relative tolerance


@dataclass
class CalibrationResult:
    target: str
    passed: bool
    detail: dict[str, Any]


def _weighted_dropout(p: pd.DataFrame) -> float:
    return float(p["dropped"].mean()) if len(p) else 0.0


def _syn_marginal(p: pd.DataFrame, col: str) -> dict[str, float]:
    return {str(k): float(v) for k, v in p.groupby(col)["dropped"].mean().items()}


def _grade_marginal(
    name: str, real: dict[str, float], syn: dict[str, float]
) -> CalibrationResult:
    """Grade one marginal dimension: per-level real vs synthetic vs +-3pp."""
    rows: list[dict[str, Any]] = []
    all_pass = True
    for level, real_v in sorted(real.items()):
        syn_v = syn.get(level)
        if syn_v is None:
            # The cohort has no coverage of this real level -> cannot grade (reported, not faked).
            rows.append(
                {
                    "level": level,
                    "real": round(real_v, 4),
                    "synthetic": None,
                    "status": "NO_COVERAGE",
                }
            )
            continue
        d = abs(syn_v - real_v)
        ok = d <= STRATUM_TOL
        all_pass = all_pass and ok
        rows.append(
            {
                "level": level,
                "real": round(real_v, 4),
                "synthetic": round(syn_v, 4),
                "abs_diff": round(d, 4),
                "tolerance": STRATUM_TOL,
                "status": "PASS" if ok else "FAIL",
            }
        )
    return CalibrationResult(name, all_pass, {"levels": rows})


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

    # Target 1: overall dropout rate within +-1pp. The cohort weights trials equally, so its
    # overall is the real TRIAL-MEAN overall (targets.overall_dropout_rate); the real
    # participant-weighted overall is recorded separately on the targets.
    syn_overall = _weighted_dropout(p)
    diff = abs(syn_overall - targets.overall_dropout_rate)
    results.append(
        CalibrationResult(
            "overall_dropout_rate",
            diff <= OVERALL_TOL,
            {
                "real": round(targets.overall_dropout_rate, 4),
                "synthetic": round(syn_overall, 4),
                "abs_diff": round(diff, 4),
                "tolerance": OVERALL_TOL,
            },
        )
    )

    # Target 2: dropout by stratum within +-3pp each, per REAL marginal dimension.
    results.append(
        _grade_marginal(
            "dropout_by_phase", targets.dropout_by_phase, _syn_marginal(p, "phase")
        )
    )
    results.append(
        _grade_marginal(
            "dropout_by_therapeutic_area",
            targets.dropout_by_therapeutic_area,
            _syn_marginal(p, "therapeutic_area"),
        )
    )
    results.append(
        _grade_marginal(
            "dropout_by_sponsor_class",
            targets.dropout_by_sponsor_class,
            _syn_marginal(p, "sponsor_class"),
        )
    )
    results.append(
        _grade_marginal(
            "dropout_by_blinding",
            targets.dropout_by_blinding,
            _syn_marginal(p, "blinding"),
        )
    )
    results.append(
        _grade_marginal(
            "dropout_by_site_count",
            targets.dropout_by_site_count,
            _syn_marginal(p, "site_band"),
        )
    )

    # Target 3: reason mix chi-square (over the ratified vocab; censoring already excluded).
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
            {
                "chisq_pvalue": float(pval),
                "threshold": CHISQ_THRESHOLD,
                "real_mix": {
                    r: round(targets.reason_mix.get(r, 0.0), 4)
                    for r in WITHDRAWAL_REASONS
                },
                "synthetic_mix": {
                    r: round(syn_counts[r] / total, 4) if total else 0.0
                    for r in WITHDRAWAL_REASONS
                },
            },
        )
    )

    # Target 4: dropout timing early fraction (hazard shape). ASSUMPTION, not fitted.
    dropped = p[p["dropped"]]
    if len(dropped):
        # Half of the participant's observed series window (engagement is capped, so use the
        # same 90-day cap the generator draws timing within).
        half = dropped["planned_duration_days"].clip(upper=90) / 2
        syn_early = float((dropped["time_to_event_days"] <= half).mean())
    else:
        syn_early = 0.0
    timing_diff = abs(syn_early - targets.early_fraction)
    results.append(
        CalibrationResult(
            "dropout_timing",
            timing_diff <= TIMING_TOL,
            {
                "assumed_early_fraction": targets.early_fraction,
                "assumption": "AACT lacks per-event timing; target is an ASSUMED early-dropout "
                "share, not estimated from real data (see the early_fraction TODO in targets.py).",
                "synthetic_early_fraction": round(syn_early, 4),
                "abs_diff": round(timing_diff, 4),
                "tolerance": TIMING_TOL,
            },
        )
    )

    # Target 5: covariate -> dropout association signs preserved (real signs).
    sign_detail: dict[str, Any] = {}
    signs_ok = True
    syn_cols = {
        "n_sites": "n_sites",
        "n_countries": "n_countries",
        "planned_duration_days": "planned_duration_days",
    }
    for cov, real_sign in targets.covariate_signs.items():
        if cov == "open_label_vs_blinded":
            open_d = _weighted_dropout(p[p["blinding"] == "OPEN_LABEL"])
            blind_d = _weighted_dropout(p[p["blinding"] == "BLINDED"])
            syn_sign = int((open_d - blind_d > 0) - (open_d - blind_d < 0))
            ok = (real_sign == 0) or (syn_sign == real_sign)
            signs_ok = signs_ok and ok
            sign_detail[cov] = {
                "real_sign": real_sign,
                "synthetic_sign": syn_sign,
                "synthetic_open": round(open_d, 4),
                "synthetic_blinded": round(blind_d, 4),
                "ok": ok,
            }
            continue
        col = syn_cols.get(cov)
        if real_sign == 0 or col is None or col not in p.columns:
            # Near-zero real association (enrollment, n_arms) carries no required sign.
            sign_detail[cov] = {"real_sign": real_sign, "synthetic_sign": 0, "ok": True}
            continue
        corr = np.corrcoef(p[col].to_numpy(float), p["dropped"].to_numpy(float))[0, 1]
        syn_sign = int(np.sign(corr)) if not np.isnan(corr) else 0
        ok = syn_sign == real_sign
        signs_ok = signs_ok and ok
        sign_detail[cov] = {
            "real_sign": real_sign,
            "synthetic_sign": syn_sign,
            "pearson_r": round(float(corr), 4) if not np.isnan(corr) else None,
            "ok": ok,
        }
    results.append(CalibrationResult("covariate_associations", signs_ok, sign_detail))

    # Target 6: enrollment & arm-count marginals per composite stratum. The cohort carries each
    # trial's REAL enrollment / n_arms, so the mean over the cohort's trials must reproduce the
    # real per-stratum mean (within rel-tol) for every stratum the cohort covers.
    enroll_misses: list[dict[str, Any]] = []
    arm_misses: list[dict[str, Any]] = []
    syn_trial_meta = p.drop_duplicates("nct_id")
    for key, real_mean in targets.enrollment_by_stratum.items():
        sub = syn_trial_meta[syn_trial_meta["stratum"] == key]
        if sub.empty:
            continue
        syn_mean = float(sub["enrollment"].mean())
        rel = abs(syn_mean - real_mean) / real_mean if real_mean else 0.0
        if rel > ENROLL_REL_TOL:
            enroll_misses.append(
                {
                    "stratum": key,
                    "real": round(real_mean, 2),
                    "synthetic": round(syn_mean, 2),
                    "rel": round(rel, 4),
                }
            )
    for key, real_mean in targets.arm_count_by_stratum.items():
        sub = syn_trial_meta[syn_trial_meta["stratum"] == key]
        if sub.empty:
            continue
        syn_mean = float(sub["n_arms"].mean())
        rel = abs(syn_mean - real_mean) / real_mean if real_mean else 0.0
        if rel > ENROLL_REL_TOL:
            arm_misses.append(
                {
                    "stratum": key,
                    "real": round(real_mean, 2),
                    "synthetic": round(syn_mean, 2),
                    "rel": round(rel, 4),
                }
            )
    results.append(
        CalibrationResult(
            "enrollment_marginals",
            len(enroll_misses) == 0 and len(arm_misses) == 0,
            {
                "tolerance_rel": ENROLL_REL_TOL,
                "enrollment_misses": enroll_misses,
                "arm_count_misses": arm_misses,
            },
        )
    )

    failed = [r.target for r in results if not r.passed]
    if failed and fail_loud:
        raise CalibrationError(
            "Synthetic cohort failed calibration on: " + ", ".join(failed)
        )
    return results


def print_table(results: list[CalibrationResult], targets: CalibrationTargets) -> None:
    """Print a per-target PASS/FAIL table with real vs synthetic vs tolerance."""
    print("=== Calibration: REAL vs SYNTHETIC vs TOLERANCE ===")
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        if r.target == "overall_dropout_rate":
            d = r.detail
            print(
                f"[{status}] overall_dropout_rate: real={d['real']} syn={d['synthetic']} "
                f"|diff|={d['abs_diff']} tol={d['tolerance']}"
            )
        elif "levels" in r.detail:
            print(f"[{status}] {r.target}:")
            for lvl in r.detail["levels"]:
                if lvl.get("status") == "NO_COVERAGE":
                    print(
                        f"    - {lvl['level']:<24} real={lvl['real']} syn=NO_COVERAGE"
                    )
                else:
                    print(
                        f"    - {lvl['level']:<24} real={lvl['real']} syn={lvl['synthetic']} "
                        f"|diff|={lvl['abs_diff']} tol={lvl['tolerance']} {lvl['status']}"
                    )
        elif r.target == "reason_mix":
            print(
                f"[{status}] reason_mix: chisq_p={r.detail['chisq_pvalue']:.4f} "
                f">= {r.detail['threshold']}"
            )
        elif r.target == "dropout_timing":
            d = r.detail
            print(
                f"[{status}] dropout_timing (ASSUMPTION): assumed={d['assumed_early_fraction']} "
                f"syn={d['synthetic_early_fraction']} |diff|={d['abs_diff']} tol={d['tolerance']}"
            )
        elif r.target == "covariate_associations":
            print(f"[{status}] covariate_associations:")
            for cov, det in r.detail.items():
                print(
                    f"    - {cov:<26} real_sign={det['real_sign']:+d} "
                    f"syn_sign={det['synthetic_sign']:+d} {'ok' if det['ok'] else 'MISS'}"
                )
        elif r.target == "enrollment_marginals":
            ne = len(r.detail.get("enrollment_misses", []))
            na = len(r.detail.get("arm_count_misses", []))
            print(
                f"[{status}] enrollment_marginals: {ne} enrollment + {na} arm-count strata "
                f"over rel-tol {r.detail['tolerance_rel']}"
            )
        else:
            print(f"[{status}] {r.target}")


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
