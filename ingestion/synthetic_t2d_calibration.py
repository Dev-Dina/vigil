"""T2D synthetic-cohort calibration report: REAL vs SYNTHETIC vs TOLERANCE, PASS/FAIL.

Grades the per-participant T2D cohort against the real (+ labelled-prior) targets
(``ingestion.synthetic_t2d_targets``) and FAILS LOUD on any miss (the task STEP 3):
  - overall guarded dropout within +-1pp;
  - per-stratum (phase / arm-type / site-band / duration-band) within +-3pp;
  - withdrawal-reason mix categorical distance below threshold;
  - dropout-timing / hazard shape (assumed early fraction);
  - covariate->dropout association SIGN matches literature + real;
  - baseline marginals for REAL covariates (imputed priors are NOT re-calibrated);
  - per-covariate provenance table (source + coverage% + imputed-flag prevalence).
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
from ingestion.synthetic_t2d import T2DSyntheticCohort
from ingestion.synthetic_t2d_targets import (
    T2DTargets,
    duration_band,
    site_band,
)
from ingestion.vocab import WITHDRAWAL_REASONS

OVERALL_TOL = 0.01  # +-1pp
STRATUM_TOL = 0.03  # +-3pp per stratum
CHISQ_THRESHOLD = 0.05  # reason-mix chi-square p-value must exceed
TIMING_TOL = 0.12  # assumed early-fraction tolerance
MARGINAL_REL_TOL = 0.08  # real-covariate baseline-marginal relative tolerance
AUC_BAND = (0.55, 0.85)  # realistic non-separability band (the honesty guard)


@dataclass
class CalibrationResult:
    target: str
    passed: bool
    detail: dict[str, Any]


def _syn_dropout_by(p: pd.DataFrame, band_col: str) -> dict[str, float]:
    return {str(k): float(v) for k, v in p.groupby(band_col)["dropped"].mean().items()}


def _grade_marginal(
    name: str, real: dict[str, float], syn: dict[str, float], tol: float = STRATUM_TOL
) -> CalibrationResult:
    rows: list[dict[str, Any]] = []
    all_pass = True
    for level, real_v in sorted(real.items()):
        syn_v = syn.get(level)
        if syn_v is None:
            rows.append(
                {
                    "level": level,
                    "real": round(real_v, 4),
                    "synthetic": None,
                    "status": "NO_COVERAGE",
                }
            )
            all_pass = False
            continue
        d = abs(syn_v - real_v)
        ok = d <= tol
        all_pass = all_pass and ok
        rows.append(
            {
                "level": level,
                "real": round(real_v, 4),
                "synthetic": round(syn_v, 4),
                "abs_diff": round(d, 4),
                "tolerance": tol,
                "status": "PASS" if ok else "FAIL",
            }
        )
    return CalibrationResult(name, all_pass, {"levels": rows})


def _bands(p: pd.DataFrame) -> pd.DataFrame:
    p = p.copy()
    p["site_band"] = p["n_sites"].map(site_band)
    p["duration_band"] = p["planned_duration_days"].map(duration_band)
    # arm_band already present from the generator; keep arm-type grade on it.
    return p


def evaluate(
    cohort: T2DSyntheticCohort, targets: T2DTargets, *, fail_loud: bool = True
) -> list[CalibrationResult]:
    p = _bands(cohort.participants)
    results: list[CalibrationResult] = []

    # 1. Overall dropout within +-1pp.
    syn_overall = float(p["dropped"].mean())
    diff = abs(syn_overall - targets.dropout_marginal)
    results.append(
        CalibrationResult(
            "overall_dropout",
            diff <= OVERALL_TOL,
            {
                "real": round(targets.dropout_marginal, 4),
                "synthetic": round(syn_overall, 4),
                "abs_diff": round(diff, 4),
                "tolerance": OVERALL_TOL,
            },
        )
    )

    # 2. Per-stratum within +-3pp: phase, arm-type, site-band, duration-band.
    results.append(
        _grade_marginal(
            "dropout_by_phase", targets.dropout_by_phase, _syn_dropout_by(p, "phase")
        )
    )
    results.append(
        _grade_marginal(
            "dropout_by_arm_type",
            targets.dropout_by_arm_type,
            _syn_dropout_by(p, "arm_band"),
        )
    )
    results.append(
        _grade_marginal(
            "dropout_by_site_band",
            targets.dropout_by_site_band,
            _syn_dropout_by(p, "site_band"),
        )
    )
    results.append(
        _grade_marginal(
            "dropout_by_duration_band",
            targets.dropout_by_duration_band,
            _syn_dropout_by(p, "duration_band"),
        )
    )

    # 3. Withdrawal-reason mix chi-square (controlled vocab; censoring already excluded).
    dropped = p[p["dropped"]]
    counts = dropped["dropout_reason"].value_counts().to_dict()
    syn_counts = {r: int(counts.get(r, 0)) for r in WITHDRAWAL_REASONS}
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

    # 4. Dropout timing / hazard shape: assumed early fraction of dropout events.
    if len(dropped):
        nvis = dropped["n_visits"].to_numpy(float)
        dvis = dropped["dropout_visit_index"].to_numpy(float)
        syn_early = float((dvis <= (nvis / 2.0)).mean())
    else:
        syn_early = 0.0
    timing_diff = abs(syn_early - targets.early_fraction_assumed)
    results.append(
        CalibrationResult(
            "dropout_timing",
            timing_diff <= TIMING_TOL,
            {
                "assumed_early_fraction": targets.early_fraction_assumed,
                "assumption": "AACT lacks per-event timing; early share is an ASSUMPTION.",
                "synthetic_early_fraction": round(syn_early, 4),
                "abs_diff": round(timing_diff, 4),
                "tolerance": TIMING_TOL,
            },
        )
    )

    # 5. Covariate->dropout association SIGNS match literature + real.
    sign_detail, signs_ok = _grade_signs(p, targets)
    results.append(CalibrationResult("covariate_signs", signs_ok, sign_detail))

    # 6. Baseline marginals for REAL covariates (imputed priors are NOT re-calibrated).
    results.append(_grade_baseline_marginals(cohort, targets))

    # 7. Per-covariate provenance + imputed-flag prevalence (report; passes if all present).
    results.append(_provenance_table(cohort, targets))

    # 8. Non-separability AUC in the realistic band (the honesty guard).
    auc = cohort.non_separability_auc
    auc_ok = AUC_BAND[0] <= auc <= AUC_BAND[1]
    results.append(
        CalibrationResult(
            "non_separability_auc",
            auc_ok,
            {
                "auc": round(auc, 4),
                "band": list(AUC_BAND),
                "note": "Engagement->dropout logistic AUC; near-1.0 == leaky planted rule.",
            },
        )
    )

    failed = [r.target for r in results if not r.passed]
    if failed and fail_loud:
        raise CalibrationError(
            "T2D synthetic cohort failed calibration on: " + ", ".join(failed)
        )
    return results


def _grade_signs(p: pd.DataFrame, targets: T2DTargets) -> tuple[dict[str, Any], bool]:
    detail: dict[str, Any] = {}
    ok_all = True

    def corr_sign(col: str, mask: pd.Series | None = None) -> tuple[int, float]:
        sub = p if mask is None else p[mask]
        x = sub[col].to_numpy(float)
        y = sub["dropped"].to_numpy(float)
        good = np.isfinite(x) & np.isfinite(y)
        if good.sum() < 3 or np.std(x[good]) == 0:
            return 0, 0.0
        r = np.corrcoef(x[good], y[good])[0, 1]
        return (int(np.sign(r)) if np.isfinite(r) else 0), float(r)

    # age (literature -1)
    s, r = corr_sign("age_years")
    detail["age_years"] = {
        "required": targets.covariate_signs["age_years"],
        "synthetic": s,
        "pearson_r": round(r, 4),
    }
    # hba1c REAL only (literature +1) — imputed values excluded from the check.
    s, r = corr_sign("hba1c_pct", mask=~p["hba1c_baseline_imputed"])
    detail["hba1c_real"] = {
        "required": targets.covariate_signs["hba1c_real"],
        "synthetic": s,
        "pearson_r": round(r, 4),
    }
    # n_sites / planned_duration (real +1)
    for cov in ("n_sites", "planned_duration_days"):
        key = "n_sites" if cov == "n_sites" else "planned_duration_days"
        s, r = corr_sign(cov)
        detail[cov] = {
            "required": targets.covariate_signs[key],
            "synthetic": s,
            "pearson_r": round(r, 4),
        }
    # placebo vs active (real sign)
    open_d = float(p[p["arm_band"] == "PLACEBO_CONTROL"]["dropped"].mean())
    act_d = float(p[p["arm_band"] == "ACTIVE_OR_EXP"]["dropped"].mean())
    s = int(np.sign(open_d - act_d))
    detail["placebo_control_vs_active"] = {
        "required": targets.covariate_signs["placebo_control_vs_active"],
        "synthetic": s,
        "placebo": round(open_d, 4),
        "active": round(act_d, 4),
    }

    for v in detail.values():
        req = v["required"]
        ok = (req == 0) or (v["synthetic"] == req)
        v["ok"] = ok
        ok_all = ok_all and ok
    return detail, ok_all


def _grade_baseline_marginals(
    cohort: T2DSyntheticCohort, targets: T2DTargets
) -> CalibrationResult:
    """Grade synthetic baseline marginals for REAL covariates against the real Table-1 means.

    Only REAL covariates are graded (age across the cohort, %female, real-where-posted HbA1c/
    BMI). Imputed HbA1c/BMI are NOT re-calibrated beyond their stated literature priors.
    """
    p = cohort.participants
    prov = targets.covariate_provenance
    rows: list[dict[str, Any]] = []
    all_pass = True

    def grade(name: str, syn_mean: float, real_mean: float) -> None:
        nonlocal all_pass
        rel = abs(syn_mean - real_mean) / real_mean if real_mean else 0.0
        ok = rel <= MARGINAL_REL_TOL
        all_pass = all_pass and ok
        rows.append(
            {
                "covariate": name,
                "real_mean": round(real_mean, 4),
                "synthetic_mean": round(syn_mean, 4),
                "rel_diff": round(rel, 4),
                "tolerance_rel": MARGINAL_REL_TOL,
                "status": "PASS" if ok else "FAIL",
            }
        )

    grade("age_years", float(p["age_years"].mean()), prov["age_years"]["mean"])
    grade(
        "pct_female", float((p["sex"] == "FEMALE").mean()), prov["pct_female"]["mean"]
    )
    real_hba = p[~p["hba1c_baseline_imputed"]]
    if len(real_hba):
        grade(
            "hba1c_real", float(real_hba["hba1c_pct"].mean()), prov["hba1c_pct"]["mean"]
        )
    real_bmi = p[~p["bmi_baseline_imputed"]]
    if len(real_bmi):
        grade("bmi_real", float(real_bmi["bmi"].mean()), prov["bmi"]["mean"])
    return CalibrationResult("baseline_marginals", all_pass, {"covariates": rows})


def _provenance_table(
    cohort: T2DSyntheticCohort, targets: T2DTargets
) -> CalibrationResult:
    """Per-covariate provenance + imputed-flag prevalence in the generated cohort."""
    p = cohort.participants
    rows = []
    for cov, meta in targets.covariate_provenance.items():
        rows.append({"covariate": cov, **meta})
    imputed = {
        "age_baseline_imputed_prevalence": round(
            float(p["age_baseline_imputed"].mean()), 4
        ),
        "hba1c_baseline_imputed_prevalence": round(
            float(p["hba1c_baseline_imputed"].mean()), 4
        ),
        "bmi_baseline_imputed_prevalence": round(
            float(p["bmi_baseline_imputed"].mean()), 4
        ),
    }
    return CalibrationResult(
        "covariate_provenance",
        True,
        {"provenance": rows, "imputed_flag_prevalence": imputed},
    )


def print_table(results: list[CalibrationResult]) -> None:
    print("=== T2D Calibration: REAL vs SYNTHETIC vs TOLERANCE ===")
    for r in results:
        status = "PASS" if r.passed else "FAIL"
        d = r.detail
        if r.target == "overall_dropout":
            print(
                f"[{status}] overall_dropout: real={d['real']} syn={d['synthetic']} "
                f"|diff|={d['abs_diff']} tol={d['tolerance']}"
            )
        elif "levels" in d:
            print(f"[{status}] {r.target}:")
            for lvl in d["levels"]:
                if lvl.get("status") == "NO_COVERAGE":
                    print(
                        f"    - {lvl['level']:<16} real={lvl['real']} syn=NO_COVERAGE"
                    )
                else:
                    print(
                        f"    - {lvl['level']:<16} real={lvl['real']} "
                        f"syn={lvl['synthetic']} |diff|={lvl['abs_diff']} "
                        f"tol={lvl['tolerance']} {lvl['status']}"
                    )
        elif r.target == "reason_mix":
            print(
                f"[{status}] reason_mix: chisq_p={d['chisq_pvalue']:.4f} "
                f">= {d['threshold']}"
            )
        elif r.target == "dropout_timing":
            print(
                f"[{status}] dropout_timing (ASSUMPTION): assumed="
                f"{d['assumed_early_fraction']} syn={d['synthetic_early_fraction']} "
                f"|diff|={d['abs_diff']} tol={d['tolerance']}"
            )
        elif r.target == "covariate_signs":
            print(f"[{status}] covariate_signs:")
            for cov, det in d.items():
                print(
                    f"    - {cov:<26} required={det['required']:+d} "
                    f"syn={det['synthetic']:+d} {'ok' if det['ok'] else 'MISS'}"
                )
        elif r.target == "baseline_marginals":
            print(f"[{status}] baseline_marginals (REAL covariates only):")
            for cov in d["covariates"]:
                print(
                    f"    - {cov['covariate']:<14} real={cov['real_mean']} "
                    f"syn={cov['synthetic_mean']} rel={cov['rel_diff']} "
                    f"{cov['status']}"
                )
        elif r.target == "covariate_provenance":
            print(f"[{status}] covariate_provenance:")
            for cov in d["provenance"]:
                print(
                    f"    - {cov['covariate']:<14} source={cov['source']:<16} "
                    f"coverage={cov['coverage_pct']}"
                )
            print(f"    imputed-flag prevalence: {d['imputed_flag_prevalence']}")
        elif r.target == "non_separability_auc":
            print(f"[{status}] non_separability_auc: auc={d['auc']} band={d['band']}")
        else:
            print(f"[{status}] {r.target}")


def write_calibration_report(
    results: list[CalibrationResult], cohort: T2DSyntheticCohort, path: Path
) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "all_passed": all(r.passed for r in results),
        "cohort": {
            "n_participants": cohort.info["n_participants"],
            "n_visit_rows": cohort.info["n_visit_rows"],
            "seed": cohort.seed,
            "hazard_intercept": cohort.hazard_intercept,
            "phase_offsets": cohort.info.get("phase_offsets", {}),
            "non_separability_auc": round(cohort.non_separability_auc, 4),
        },
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
