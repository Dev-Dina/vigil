"""One command: build the deterministic T2D synthetic per-participant cohort (build-time only).

    uv run python scripts/build_synthetic_t2d.py

Selects the canonical T2D modelling cohort (755 trials / 2,402 usable arms; fails loud on
drift), computes the real (+ labelled-prior) calibration targets, generates the seeded
per-participant cohort with per-visit engagement trajectories (provenance layers 3 + 4), then
grades it against every target and FAILS LOUD on any miss (the task STEP 3) or if the
engagement->dropout map is near-separable (the honesty guard).

Writes to ``data/synthetic/t2d/`` (a NEW subdir; never touches the existing whole-cohort
artifacts). Outputs: participants.parquet, engagement.parquet, calibration_targets.json,
calibration_report.json, README.md. ``data/`` is git-ignored — never committed.

ClinicalTrials.gov / AACT is build-time only; this reads the pinned raw snapshot + cleaned
ref_* tables on disk, never a live/runtime call, never reached by an agent. No PHI (synthetic).
"""

from __future__ import annotations

import sys
from pathlib import Path

from ingestion import SYNTHETIC_SEED
from ingestion.errors import CalibrationError
from ingestion.synthetic_t2d import DISCLAIMER, generate
from ingestion.synthetic_t2d_calibration import (
    evaluate,
    print_table,
    write_calibration_report,
)
from ingestion.synthetic_t2d_targets import (
    BMI_PRIOR_MEAN,
    HBA1C_PRIOR_MEAN,
    T2D_SYNTHETIC_ROOT,
    compute_targets,
    select_cohort,
    write_targets,
)


def _write_readme(out_root: Path, cohort, targets) -> Path:
    prov = targets.covariate_provenance
    text = f"""# T2D synthetic cohort

{DISCLAIMER}

This is a build-time, method-validity artifact: a per-participant Type-2-diabetes cohort whose
aggregates reproduce the REAL T2D AACT statistics, with per-visit engagement trajectories for a
sequence model to learn from. It contains NO real participants and NO PHI.

## Cohort
- Selection: INDUSTRY lead, drug/biologic, modelling phases {{PHASE1/PHASE2, PHASE2,
  PHASE2/PHASE3, PHASE3}}, T2D MeSH ("Diabetes Mellitus, Type 2"), >=1 usable arm
  (started>0 & completed not null). Reproduces EXACTLY {targets.n_trials} trials /
  {targets.n_usable_arms} usable arms (fails loud on drift).
- Synthetic cohort size: {cohort.info["n_participants"]} participants,
  {cohort.info["n_visit_rows"]} per-visit engagement rows. Per arm we synthesise
  min({cohort.info["max_per_arm"]}, started) participants (>= {cohort.info["min_per_arm"]}); the
  real summed `started` (~580k) exceeds the documented cap, so each arm is capped at
  {cohort.info["max_per_arm"]}.
- Dropout target guard: per-arm dropout marginals exclude arms with started<20 OR
  dropout_rate==1.0 (all-lost arms are label-suspect). Guarded marginal =
  {targets.dropout_marginal:.4f}.

## Determinism
Seed: {SYNTHETIC_SEED} (`from ingestion import SYNTHETIC_SEED`). All randomness derives from a
single numpy.random.Generator(seed); regeneration is bit-identical. Every participant row and
every engagement row carries `synthetic = True`.

## Provenance layers
- Layer 3 (baseline covariates): per participant, sampled from the calibrated joint, HARD-
  BOUNDED by the trial's eligibility ranges (min/max age; sex per real %female). Imputed
  HbA1c / BMI are sampled as MARGINALS ONLY (no invented correlation) and are EXCLUDED from the
  hazard. Real-where-posted HbA1c may enter the hazard.
- Layer 4 (per-visit engagement): over the trial's derived visit schedule (one visit per
  ~{cohort.info["visit_interval_days"]} days of planned duration; documented default when
  absent). Per-visit miss probability is a hazard over REAL covariates, arm, cumulative missed
  visits and trial burden. Dropout fires probabilistically with a CAMP-style consecutive-miss
  boost ({cohort.info["consecutive_miss_drop"]}+ consecutive misses raise — not force — the
  hazard). Observations stop at the dropout visit (no post-dropout rows), so no future/outcome
  data leaks.

## Per-covariate provenance + coverage
| covariate | source | coverage | mean | imputed flag |
|---|---|---|---|---|
| age | {prov["age_years"]["source"]} | {prov["age_years"]["coverage_pct"]:.1%} | {prov["age_years"]["mean"]:.1f} y | `age_baseline_imputed` |
| sex (%female) | {prov["pct_female"]["source"]} | {prov["pct_female"]["coverage_pct"]:.1%} | {prov["pct_female"]["mean"]:.1%} | — |
| HbA1c | {prov["hba1c_pct"]["source"]} | {prov["hba1c_pct"]["coverage_pct"]:.1%} | {prov["hba1c_pct"]["mean"]:.2f}% | `hba1c_baseline_imputed` |
| BMI | {prov["bmi"]["source"]} | {prov["bmi"]["coverage_pct"]:.1%} | {prov["bmi"]["mean"]:.1f} | `bmi_baseline_imputed` |

Imputed values use LABELLED literature priors: HbA1c ~{HBA1C_PRIOR_MEAN}% (7.5-8.5% band),
BMI ~{BMI_PRIOR_MEAN} kg/m^2 (30-32 band). The `*_imputed` flags mark which participants carry a
prior-sampled value; imputed HbA1c/BMI never drive dropout.

## Stated assumption: trajectory -> dropout
The deteriorating-engagement -> dropout mapping is a STATED ASSUMPTION (real per-event timing
does not exist in AACT). It is realistic-strength and NOISY: attendance and the dropout event
share a latent disengagement state but each carries its OWN independent noise, so the trajectory
CORRELATES with dropout without DETERMINING it. A non-separability check fits a logistic
classifier on the engagement features and asserts the AUC sits in a realistic band
(<= 0.85): **AUC = {cohort.non_separability_auc:.3f}** here. A near-perfect AUC would mean a
leaky planted deterministic rule and fails the build loud.

## Literature-fixed effect signs
younger age -> more dropout (-); higher REAL baseline HbA1c -> more dropout (+); placebo/control
arm -> more dropout (+); denser schedule & longer planned duration -> more dropout (+); more
sites -> more dropout (+, real T2D sign).

## Files
- `participants.parquet` — one row per synthetic participant (baseline covariates, imputed
  flags, dropped/censored, dropout reason, dropout visit).
- `engagement.parquet` — long per-visit attendance trajectory.
- `calibration_targets.json` — the real (+ labelled-prior) targets.
- `calibration_report.json` — REAL vs SYNTHETIC vs TOLERANCE, PASS/FAIL.
"""
    out_root.mkdir(parents=True, exist_ok=True)
    path = out_root / "README.md"
    path.write_text(text, encoding="utf-8")
    return path


def main(argv: list[str] | None = None) -> int:
    out_root = T2D_SYNTHETIC_ROOT

    cohort_sel = select_cohort()
    targets, real_values = compute_targets(cohort_sel)
    targets_path = write_targets(targets, out_root)

    cohort = generate(cohort_sel, targets, real_values, seed=SYNTHETIC_SEED)

    cohort.participants.to_parquet(out_root / "participants.parquet", index=False)
    cohort.visits.to_parquet(out_root / "engagement.parquet", index=False)
    readme_path = _write_readme(out_root, cohort, targets)

    # Grade every target, print, write report BEFORE failing loud (operator sees the full table).
    results = evaluate(cohort, targets, fail_loud=False)
    print_table(results)
    report_path = write_calibration_report(
        results, cohort, out_root / "calibration_report.json"
    )
    all_passed = all(r.passed for r in results)

    print("\n=== T2D synthetic build ===")
    print(
        f"cohort:          {targets.n_trials} trials / {targets.n_usable_arms} usable arms"
    )
    print(f"participants:    {cohort.info['n_participants']}")
    print(f"engagement rows: {cohort.info['n_visit_rows']}")
    print(f"seed:            {cohort.seed}")
    print(
        f"dropout marginal: real={targets.dropout_marginal:.4f} "
        f"syn={cohort.info['overall_dropout_synthetic']:.4f}"
    )
    print(f"non-separability AUC: {cohort.non_separability_auc:.4f}")
    print(f"targets:         {targets_path}")
    print(f"report:          {report_path}")
    print(f"readme:          {readme_path}")
    print("calibration:     " + ("ALL PASS" if all_passed else "FAIL"))

    if not all_passed:
        failed = [r.target for r in results if not r.passed]
        raise CalibrationError("T2D calibration FAILED on: " + ", ".join(failed))
    print(
        "DONE: T2D synthetic cohort generated, calibrated (all PASS), spec-conformant."
    )
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except CalibrationError as exc:
        print(f"T2D BUILD FAIL (calibration): {exc}", file=sys.stderr)
        sys.exit(3)
    except ValueError as exc:
        print(f"T2D BUILD FAIL (cohort drift): {exc}", file=sys.stderr)
        sys.exit(2)
