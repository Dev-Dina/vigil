"""Augment the T2D synthetic cohort with calendar geometry + right-censoring.

    uv run python scripts/augment_t2d_survival.py

Adds enrollment_day, time_to_event, event_observed to participants.parquet (in-place),
writes engagement_censored.parquet, and produces calibration_report_v2.json.

Fails loud on:
  - missing base files (run build_synthetic_t2d.py first)
  - informative censoring (|r| > 0.15 between censored indicator and biology/signal)
  - latent dropout rate drifted from the calibrated value
  - row-count change in participants.parquet
  - non-deterministic enrollment_day

ClinicalTrials.gov / AACT is build-time only. No PHI. DO NOT COMMIT the data/ directory.
"""

from __future__ import annotations

import sys

from ingestion.errors import CalibrationError
from ingestion.synthetic_t2d_augment import run_augmentation
from ingestion.synthetic_t2d_targets import T2D_SYNTHETIC_ROOT
from ingestion.config import CLEAN_ROOT


def main() -> int:
    print("=== T2D Survival Augmentation ===")
    print(f"synthetic_root : {T2D_SYNTHETIC_ROOT}")
    print(f"clean_root     : {CLEAN_ROOT}")
    print()

    diag = run_augmentation(
        synthetic_root=T2D_SYNTHETIC_ROOT,
        clean_root=CLEAN_ROOT,
    )

    # --- Print diagnostics ---
    cd = diag["censoring_diagnostics"]
    ni = diag["non_informative_censoring"]
    ld = diag["latent_dropout_check"]

    print("--- Latent dropout check ---")
    print(
        f"  mean(dropped): {ld['synthetic_latent']:.4f}  "
        f"real: {ld['real']:.4f}  "
        f"|diff|: {ld['abs_diff']:.4f}  tol: {ld['tolerance']}  [{ld['status']}]"
    )
    print()

    print("--- Observed / censored / completer split ---")
    print(f"  n_total                     : {cd['n_total']}")
    print(f"  n_event_observed (dropout)  : {cd['n_event_observed']}")
    print(f"  n_admin_censored            : {cd['n_admin_censored']}")
    print(f"  n_dropout_censored_by_cutoff: {cd['n_dropout_censored_by_cutoff']}")
    print(f"  n_completers                : {cd['n_completers']}")
    print(f"  latent_dropout_rate         : {cd['latent_dropout_rate']:.4f}")
    print(f"  observed_event_rate         : {cd['observed_event_rate']:.4f}")
    print(f"  note: {cd['note']}")
    print()

    pct = cd["censoring_time_percentiles_among_event_observed_0"]
    if pct:
        print("--- Censoring-time distribution (among censored) ---")
        for k, v in pct.items():
            print(f"  {k}: {v:.1f} days")
        print()

    print("--- Non-informative censoring check ---")
    for row in ni["correlations"]:
        r_str = f"{row['r']:+.6f}" if row["r"] is not None else "N/A"
        p_str = f"{row['pvalue']:.4f}" if row["pvalue"] is not None else "N/A"
        print(
            f"  {row['covariate']:<20} r={r_str}  p={p_str}  [{row['status']}]"
        )
    print()

    print("--- Engagement truncation ---")
    print(f"  engagement rows before : {diag['engagement_rows_before']}")
    print(f"  engagement rows after  : {diag['engagement_rows_after']}")
    print()

    print("--- Outputs ---")
    for k, v in diag["outputs"].items():
        print(f"  {k}: {v}")
    print()

    print("--- Summary ---")
    print(f"  participants.parquet row count: {diag['participants_row_count']} (unchanged)")
    status = "ALL PASS" if diag["v2_all_passed"] else "FAIL"
    print(f"  calibration_report_v2 : {status}")
    print()
    print("DONE: T2D survival augmentation complete.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except CalibrationError as exc:
        print(f"AUGMENTATION FAIL (calibration): {exc}", file=sys.stderr)
        sys.exit(3)
    except ValueError as exc:
        print(f"AUGMENTATION FAIL (validation): {exc}", file=sys.stderr)
        sys.exit(2)
    except FileNotFoundError as exc:
        print(f"AUGMENTATION FAIL (missing file): {exc}", file=sys.stderr)
        sys.exit(1)
