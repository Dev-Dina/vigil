"""One command: raw -> clean -> synthetic, emit reports, validate against the spec.

    uv run python -m ingestion.pipeline            # run on the committed sample fixture
    uv run python -m ingestion.pipeline --live     # extract from a live AACT snapshot

Build-time only. No live DB calls at runtime, never reached by an agent.
"""

from __future__ import annotations

import argparse
import json
import sys
from dataclasses import dataclass
from pathlib import Path

from ingestion import SYNTHETIC_SEED
from ingestion.calibration import evaluate, write_calibration_report
from ingestion.clean import clean_snapshot
from ingestion.config import (
    AACT_SOURCE_URL,
    CLEAN_ROOT,
    FIXTURE_ROOT,
    REPORT_ROOT,
    SYNTHETIC_ROOT,
    AactConnection,
)
from ingestion.errors import LeakageError
from ingestion.extract import extract_to_raw
from ingestion.features import (
    assert_no_leakage,
    build_features,
    fit_scaler_on_train,
    group_split_by_trial,
)
from ingestion.report import QualityReport
from ingestion.synthetic import COVARIATE_ASSUMPTIONS, generate, write_synthetic
from ingestion.targets import compute_targets


@dataclass
class PipelineResult:
    snapshot_dir: Path
    quality_report: Path
    calibration_report: Path
    synthetic_manifest: Path
    all_calibrations_passed: bool
    n_trials: int
    n_arms: int
    n_participants: int
    n_feature_samples: int


def run(
    *,
    live: bool = False,
    fail_loud: bool = True,
    out_clean: Path = CLEAN_ROOT,
    out_synth: Path = SYNTHETIC_ROOT,
    out_reports: Path = REPORT_ROOT,
) -> PipelineResult:
    # --- raw -------------------------------------------------------------------------
    if live:
        conn = AactConnection.from_env()
        snapshot_dir = extract_to_raw(conn)
    else:
        snapshot_dir = FIXTURE_ROOT  # committed SAMPLE fixture

    # --- clean -----------------------------------------------------------------------
    report = QualityReport()
    report.notes.append(f"source_url={AACT_SOURCE_URL}")
    report.notes.append(f"snapshot_dir={snapshot_dir.name}")
    frames = clean_snapshot(
        snapshot_dir, report=report, fail_loud=fail_loud, out_root=out_clean
    )
    quality_path = report.write(out_reports / "data_quality_report.json")

    trials, arms, withdrawals = (
        frames["ref_trial"],
        frames["ref_arm"],
        frames["ref_withdrawal_reason"],
    )

    # --- synthetic -------------------------------------------------------------------
    targets = compute_targets(trials, arms, withdrawals)
    cohort = generate(trials, arms, targets, seed=SYNTHETIC_SEED)
    write_synthetic(cohort, out_root=out_synth)

    synth_manifest = _write_synthetic_manifest(out_synth, targets, cohort.seed)

    results = evaluate(cohort, targets, fail_loud=fail_loud)
    calib_path = write_calibration_report(
        results, out_reports / "calibration_report.json"
    )
    all_passed = all(r.passed for r in results)

    # --- features + leakage check ----------------------------------------------------
    fm = build_features(cohort.participants, cohort.engagement)
    splits = group_split_by_trial(fm.X, seed=SYNTHETIC_SEED)
    fit_scaler_on_train(splits, fm.feature_columns)  # scalers fit on train only
    assert_no_leakage(fm, splits)

    return PipelineResult(
        snapshot_dir=snapshot_dir,
        quality_report=quality_path,
        calibration_report=calib_path,
        synthetic_manifest=synth_manifest,
        all_calibrations_passed=all_passed,
        n_trials=len(trials),
        n_arms=len(arms),
        n_participants=len(cohort.participants),
        n_feature_samples=len(fm.X),
    )


def _write_synthetic_manifest(out_synth: Path, targets, seed: int) -> Path:
    out_synth.mkdir(parents=True, exist_ok=True)
    manifest = {
        "synthetic_seed": seed,
        "deterministic": "single numpy.random.Generator(seed); bit-identical regen",
        "disclaimer": "SYNTHETIC — calibrated to aggregate AACT statistics, NOT real "
        "participants, method-validity only, no PHI.",
        "labelled_assumptions": COVARIATE_ASSUMPTIONS,
        "calibration_targets": targets.to_dict(),
    }
    path = out_synth / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return path


def validate_done(result: PipelineResult) -> list[str]:
    """The spec's "Done when": one command ran raw->clean->synthetic, emitted a quality
    report, and validated. Returns a list of problems (empty = conformant)."""
    problems: list[str] = []
    if not result.quality_report.exists():
        problems.append("data-quality report not emitted")
    if not result.calibration_report.exists():
        problems.append("calibration report not emitted")
    if not (CLEAN_ROOT / "ref_trial.parquet").exists():
        problems.append("ref_trial.parquet missing")
    if not (CLEAN_ROOT / "ref_arm.parquet").exists():
        problems.append("ref_arm.parquet missing")
    if not (CLEAN_ROOT / "ref_withdrawal_reason.parquet").exists():
        problems.append("ref_withdrawal_reason.parquet missing")
    if not (SYNTHETIC_ROOT / "README.md").exists():
        problems.append("synthetic README disclaimer missing")
    if not result.all_calibrations_passed:
        problems.append("synthetic cohort failed one or more calibration targets")
    if result.n_participants <= 0:
        problems.append("no synthetic participants generated")
    return problems


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Vigil data pipeline (Phase 1)")
    parser.add_argument(
        "--live",
        action="store_true",
        help="Extract from a live AACT snapshot (env-configured) instead of the fixture.",
    )
    parser.add_argument(
        "--no-fail-loud",
        action="store_true",
        help="Collect every issue into the reports instead of aborting on the first.",
    )
    args = parser.parse_args(argv)

    try:
        result = run(live=args.live, fail_loud=not args.no_fail_loud)
    except LeakageError as exc:
        print(f"PIPELINE FAIL (leakage): {exc}", file=sys.stderr)
        return 2

    problems = validate_done(result)
    print("=== Vigil data pipeline ===")
    print(f"snapshot:        {result.snapshot_dir.name}")
    print(f"ref_trial:       {result.n_trials} rows")
    print(f"ref_arm:         {result.n_arms} rows")
    print(f"synthetic:       {result.n_participants} participants")
    print(f"feature samples: {result.n_feature_samples}")
    print(f"quality report:  {result.quality_report}")
    print(f"calibration:     {result.calibration_report}")
    print(
        "calibration:     " + ("ALL PASS" if result.all_calibrations_passed else "FAIL")
    )
    if problems:
        print("DONE-WHEN: FAIL")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("DONE-WHEN: PASS (raw->clean->synthetic, reports emitted, spec-conformant)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
