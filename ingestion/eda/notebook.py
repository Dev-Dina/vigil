"""Marimo notebook: interactive view of the real AACT reference-cohort EDA.

Read-only display of what the pipeline already produced under ``data/eda/`` and the cleaned
``ref_*`` parquet. It computes nothing about the pipeline and mutates no data. Run:

    uv run marimo edit ingestion/eda/notebook.py

If ``data/eda/`` / ``data/clean/`` are empty, generate them first with ``make eda-report``
(``uv run python -m ingestion.eda``) on the cleaned ``ref_*`` tables.
"""

import marimo

__generated_with = "0.23.9"
app = marimo.App(width="medium")


@app.cell
def _imports():
    from pathlib import Path

    import marimo as mo
    import pandas as pd

    return Path, mo, pd


@app.cell
def _paths(Path):
    try:
        repo_root = Path(__file__).resolve().parents[2]
    except NameError:
        repo_root = Path.cwd()
    eda_dir = repo_root / "data" / "eda"
    clean_dir = repo_root / "data" / "clean"
    figures_dir = eda_dir / "figures"
    return clean_dir, eda_dir, figures_dir


@app.cell
def _title(mo):
    mo.md("""
    # Vigil — Real AACT Reference Cohort EDA

    Build-time snapshot only (ClinicalTrials.gov / AACT, pinned). This is **public
    reference data, no PHI**; it calibrates the synthetic cohort and proves method
    validity — never a clinical claim. Numbers below are read live from
    `data/clean/ref_*.parquet` and `data/eda/`.
    """)
    return


@app.cell
def _headline(clean_dir, mo, pd):
    ref_trial_path = clean_dir / "ref_trial.parquet"
    ref_arm_path = clean_dir / "ref_arm.parquet"
    if ref_trial_path.exists() and ref_arm_path.exists():
        trials = pd.read_parquet(ref_trial_path)
        arms = pd.read_parquet(ref_arm_path)
        headline = mo.md(
            f"""
            ## Post-filter study count

            - Raw studies after the population filter (interventional + posted
              participant-flow): **73,833**
            - Cleaned `ref_trial` rows after fail-loud validation drops: **{len(trials):,}**
            - `ref_arm` rows (one per trial-arm): **{len(arms):,}**
            - Arm-level dropout — trial-mean **{arms["dropout_rate"].mean():.4f}**,
              participant-weighted
              **{(arms["not_completed"].sum() / arms["started"].sum()):.4f}**

            **Decision:** raw vs cleaned are stated separately — the 760 dropped rows are
            genuine missing/invalid source data (fail-loud), never silently coerced.
            """
        )
    else:
        headline = mo.md(
            "_`data/clean/ref_*.parquet` not found. Run the pipeline on real data, "
            "then `make eda-report`._"
        )
    headline
    return


@app.cell
def _summary(eda_dir, mo):
    summary_md_path = eda_dir / "eda_summary.md"
    if summary_md_path.exists():
        summary_view = mo.vstack(
            [
                mo.md("## Computed EDA summary (`data/eda/eda_summary.md`)"),
                mo.md(summary_md_path.read_text(encoding="utf-8")),
            ]
        )
    else:
        summary_view = mo.md(
            "⚠️ No `data/eda/eda_summary.md` — run `make eda-report` first."
        )
    summary_view
    return


@app.cell
def _figures(figures_dir, mo):
    # Each figure paired with the decision it illustrates (qualitative; numbers live above).
    captions = {
        "dropout_rate_hist.png": (
            "**Dropout distribution.** Right-skewed; trial-mean ≈ 0.20 sits above the "
            "median — a long tail of high-dropout trials. This is the target the synthetic "
            "cohort must reproduce."
        ),
        "dropout_by_phase.png": (
            "**By phase.** Mid-phase (PHASE1/PHASE2, PHASE2) run highest; PHASE4 and "
            "EARLY_PHASE1 lowest. Phase is a stratum for calibration."
        ),
        "dropout_by_therapeutic_area.png": (
            "**By therapeutic area.** Oncology highest by a wide margin; ophthalmology / "
            "infectious-disease lowest. Therapeutic area is a calibration stratum."
        ),
        "dropout_by_enrollment_size.png": (
            "**By enrollment size.** Roughly flat — dropout is not primarily an "
            "enrollment-size effect; site/country count and duration drive it instead."
        ),
        "enrollment_hist.png": (
            "**Enrollment.** Heavy-tailed toward small trials; matched per stratum so the "
            "synthetic marginals line up."
        ),
        "withdrawal_reason_mix.png": (
            "**Withdrawal-reason mix.** Controlled vocab incl. `STUDY_TERMINATED` (sponsor/"
            "DSMB) and `ADMINISTRATIVE` (bookkeeping); `OTHER` ≈ 17.5% is the honest floor. "
            "Censoring / still-ongoing is **excluded** from the mix, not bucketed as OTHER."
        ),
        "missingness.png": (
            "**Missingness.** Per-field null rates on the cleaned `ref_*`; nullable fields "
            "(e.g. actual duration, max age) are expected, not errors."
        ),
    }
    items = []
    for fname, caption in captions.items():
        fpath = figures_dir / fname
        if fpath.exists():
            items.append(mo.vstack([mo.md(caption), mo.image(str(fpath))]))
    figures_view = (
        mo.vstack([mo.md("## Figures"), *items])
        if items
        else mo.md("_No figures found — run `make eda-report`._")
    )
    figures_view
    return


@app.cell
def _leakage_note(mo):
    mo.md("""
    ## Modelling decisions carried from this EDA

    - **Covariate signs (real):** ↑ dropout with more sites, more countries, longer
      planned duration; blinded < open-label. These signs must survive in the synthetic
      cohort.
    - **Strata:** phase × therapeutic_area × sponsor_class (+ blinded, single/multi-site)
      drive calibration targets.
    - **Censoring is not dropout:** right-censored / still-ongoing participants are
      excluded from labels they cannot have — never labelled "no dropout".
    - **Group split by trial** (`nct_id`) so one trial never spans train/val/test; scalers
      fit on train only. `synthetic` is metadata, never a feature.
    """)
    return


if __name__ == "__main__":
    app.run()
