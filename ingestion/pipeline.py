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
from ingestion.calibration import evaluate, print_table, write_calibration_report
from ingestion.clean import clean_snapshot
from ingestion.config import (
    AACT_SOURCE_URL,
    CLEAN_FIXTURE_ROOT,
    CLEAN_ROOT,
    GOLDEN_RAW_ROOT,
    REPORT_FIXTURE_ROOT,
    REPORT_ROOT,
    SYNTHETIC_ROOT,
    AactConnection,
)
from ingestion.errors import CalibrationError, ClobberGuardError, LeakageError
from ingestion.extract import extract_to_raw
from ingestion.features import (
    assert_no_leakage,
    build_features,
    fit_scaler_on_train,
    group_split_by_trial,
)
from ingestion.report import QualityReport
from ingestion.synthetic import (
    COVARIATE_ASSUMPTIONS,
    PARTICIPANTS_PER_TRIAL,
    generate,
    subsample_trials,
    write_synthetic,
)
from ingestion.targets import compute_targets


@dataclass
class PipelineResult:
    snapshot_dir: Path
    clean_dir: Path
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
    out_clean: Path | None = None,
    out_synth: Path = SYNTHETIC_ROOT,
    out_reports: Path | None = None,
) -> PipelineResult:
    # --- raw -------------------------------------------------------------------------
    if live:
        conn = AactConnection.from_env()
        snapshot_dir = extract_to_raw(conn)
    else:
        # The committed GOLDEN SET (REAL public AACT slice) is the non-live substrate, so the
        # pipeline runs end-to-end without a live AACT Postgres.
        snapshot_dir = GOLDEN_RAW_ROOT

    # A live run produces the REAL ref_* snapshot in CLEAN_ROOT. A non-live run cleans into a
    # separate dir so it never clobbers a real snapshot already on disk (which is the preferred
    # synthetic-generation source and the EDA input).
    if out_clean is None:
        out_clean = CLEAN_ROOT if live else CLEAN_FIXTURE_ROOT

    # Fail loud on an explicit/erroneous override too: a non-live run must never target the
    # REAL clean snapshot (the synthetic-generation source and EDA input).
    if not live and out_clean.resolve() == CLEAN_ROOT.resolve():
        raise ClobberGuardError(
            f"refusing to overwrite the REAL clean snapshot at {CLEAN_ROOT} on a non-live "
            f"run (live=False); a non-live run must target CLEAN_FIXTURE_ROOT "
            f"({CLEAN_FIXTURE_ROOT})."
        )

    # Same guard for the reports: a non-live run must never overwrite the REAL reports (the
    # quality/calibration artifacts describing the real cohort — EDA + audit inputs).
    out_reports = _resolve_report_root(live=live, out_reports=out_reports)

    # The preferred synthetic-generation source: the REAL cleaned ref_* tables in CLEAN_ROOT, so
    # the cohort's stratum mix mirrors the real AACT population and reproduces the EDA marginal
    # targets. Captured here (a fixture run writes elsewhere, leaving these intact).
    real_source = _load_real_reference(CLEAN_ROOT)

    # --- clean -----------------------------------------------------------------------
    report = QualityReport()
    report.notes.append(f"source_url={AACT_SOURCE_URL}")
    report.notes.append(f"snapshot_dir={snapshot_dir.name}")
    frames = clean_snapshot(
        snapshot_dir,
        report=report,
        out_root=out_clean,
        live=live,
        fail_loud=fail_loud,
    )
    quality_path = report.write(out_reports / "data_quality_report.json")

    trials, arms, withdrawals = (
        frames["ref_trial"],
        frames["ref_arm"],
        frames["ref_withdrawal_reason"],
    )

    # --- synthetic -------------------------------------------------------------------
    # Targets are the REAL captured-EDA aggregates (compute_targets reads data/eda). The cohort
    # is generated from the REAL clean ref_* tables when present (stratified subsample so the
    # stratum mix mirrors the real population and the marginal targets are reproduced) — the
    # cleaned snapshot above is only the fallback substrate (e.g. fixture-only environments).
    gen_trials, gen_arms, gen_withdrawals, gen_source = _generation_source(
        real_source, trials, arms, withdrawals
    )
    # Scaffold (composite-stratum enrollment/arm means) is built from the GENERATION frames so
    # target 6 grades the cohort against its own trials' real marginals; the real overall /
    # marginal / reason / sign targets come from the captured EDA regardless.
    targets = compute_targets(gen_trials, gen_arms, gen_withdrawals)
    cohort = generate(
        gen_trials,
        gen_arms,
        targets,
        seed=SYNTHETIC_SEED,
        participants_per_trial=PARTICIPANTS_PER_TRIAL,
    )
    write_synthetic(cohort, out_root=out_synth, generation_source=gen_source)
    report.notes.append(
        f"synthetic_generation_trials={len(gen_trials)} "
        f"participants_per_trial={PARTICIPANTS_PER_TRIAL}"
    )

    synth_manifest = _write_synthetic_manifest(
        out_synth,
        targets,
        cohort.seed,
        n_gen_trials=len(gen_trials),
        generation_source=gen_source,
    )

    # Always compute every target, print the table, and write the report BEFORE failing loud,
    # so the operator sees the full PASS/FAIL breakdown even on a miss.
    results = evaluate(cohort, targets, fail_loud=False)
    print_table(results, targets)
    calib_path = write_calibration_report(
        results, out_reports / "calibration_report.json"
    )
    all_passed = all(r.passed for r in results)
    failed = [r.target for r in results if not r.passed]
    if failed and fail_loud:
        raise CalibrationError(
            "Synthetic cohort failed calibration on: " + ", ".join(failed)
        )

    # --- features + leakage check ----------------------------------------------------
    fm = build_features(cohort.participants, cohort.engagement)
    splits = group_split_by_trial(fm.X, seed=SYNTHETIC_SEED)
    fit_scaler_on_train(splits, fm.feature_columns)  # scalers fit on train only
    assert_no_leakage(fm, splits)

    # The ref_* tables the cohort was actually generated from live in CLEAN_ROOT when a real
    # snapshot was used, else in the fixture clean dir.
    ref_dir = CLEAN_ROOT if real_source is not None else out_clean

    return PipelineResult(
        snapshot_dir=snapshot_dir,
        clean_dir=ref_dir,
        quality_report=quality_path,
        calibration_report=calib_path,
        synthetic_manifest=synth_manifest,
        all_calibrations_passed=all_passed,
        n_trials=len(gen_trials),
        n_arms=len(gen_arms),
        n_participants=len(cohort.participants),
        n_feature_samples=len(fm.X),
    )


def _resolve_report_root(*, live: bool, out_reports: Path | None) -> Path:
    """Pick the reports dir, refusing to clobber the REAL reports on a non-live run.

    The real ``data/reports`` artifacts describe the real cohort (EDA + audit inputs). A
    non-live run defaults to ``REPORT_FIXTURE_ROOT`` and fails loud if explicitly pointed at
    ``REPORT_ROOT`` — the same fail-loud pattern that protects the real clean snapshot.
    """
    if out_reports is None:
        return REPORT_ROOT if live else REPORT_FIXTURE_ROOT
    if not live and out_reports.resolve() == REPORT_ROOT.resolve():
        raise ClobberGuardError(
            f"refusing to overwrite the REAL reports at {REPORT_ROOT} on a non-live run "
            f"(live=False); a non-live run must target REPORT_FIXTURE_ROOT "
            f"({REPORT_FIXTURE_ROOT})."
        )
    return out_reports


def _load_real_reference(clean_root: Path):
    """Read the REAL cleaned ``ref_*`` parquet tables if present, else ``None``."""
    import pandas as pd

    paths = {
        name: clean_root / f"{name}.parquet"
        for name in ("ref_trial", "ref_arm", "ref_withdrawal_reason")
    }
    if not all(p.exists() for p in paths.values()):
        return None
    return (
        pd.read_parquet(paths["ref_trial"]),
        pd.read_parquet(paths["ref_arm"]),
        pd.read_parquet(paths["ref_withdrawal_reason"]),
    )


def _generation_source(real_source, trials, arms, withdrawals):
    """Pick the trial set the synthetic cohort is generated from.

    Prefer the REAL ``ref_*`` snapshot captured before the clean stage (so the cohort's stratum
    mix mirrors the real population and reproduces the EDA marginal targets); subsample it
    (stratified, deterministic) to stay tractable. Use it only if materially larger than this
    run's cleaned frames (i.e. a real snapshot, not the same small fixture). Otherwise fall back
    to the frames just cleaned in this run.

    Returns ``(trials, arms, withdrawals, source)`` where ``source`` is ``"real"`` or
    ``"sample"`` — stamped into the synthetic manifest/README so a cohort is never ambiguous.
    """
    if real_source is not None:
        rt, ra, rw = real_source
        if len(rt) > len(trials):
            st, sa, sw = subsample_trials(rt, ra, rw, seed=SYNTHETIC_SEED)
            return st, sa, sw, "real"
    return trials, arms, withdrawals, "sample"


def _write_synthetic_manifest(
    out_synth: Path, targets, seed: int, *, n_gen_trials: int, generation_source: str
) -> Path:
    out_synth.mkdir(parents=True, exist_ok=True)
    manifest = {
        "synthetic_seed": seed,
        "generation_source": generation_source,  # "real" | "sample" — never ambiguous
        "deterministic": "single numpy.random.Generator(seed); bit-identical regen",
        "participants_per_trial": PARTICIPANTS_PER_TRIAL,
        "generation_trials": n_gen_trials,
        "generation_note": "Each real trial contributes an equal number of participants whose "
        "dropper fraction equals that trial's real participant-weighted rate, so the cohort "
        "reproduces the real TRIAL-MEAN marginal targets.",
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
    for tbl in ("ref_trial", "ref_arm", "ref_withdrawal_reason"):
        if not (result.clean_dir / f"{tbl}.parquet").exists():
            problems.append(f"{tbl}.parquet missing")
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
    except CalibrationError as exc:
        print(f"PIPELINE FAIL (calibration): {exc}", file=sys.stderr)
        return 3

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
