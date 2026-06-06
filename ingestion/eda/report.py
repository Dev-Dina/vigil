"""EDA over the real ``ref_*`` tables: figures + JSON/markdown summary.

Every figure and statistic is computed from the validated Parquet tables produced by the
clean stage, so this runs only on data that already passed the spec's fail-loud validation.
The trial-level dropout rate is the participant-weighted mean of its arms' dropout
(``sum(not_completed) / sum(started)``), the same quantity the synthetic cohort calibrates to.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # headless, deterministic; build-time only
import matplotlib.pyplot as plt
import numpy as np
import pandas as pd

from ingestion.config import CLEAN_ROOT, DATA_ROOT

EDA_ROOT = DATA_ROOT / "eda"
FIG_ROOT = EDA_ROOT / "figures"


@dataclass(frozen=True)
class EdaInputs:
    trials: pd.DataFrame
    arms: pd.DataFrame
    withdrawals: pd.DataFrame


def load_clean(clean_root: Path = CLEAN_ROOT) -> EdaInputs:
    """Load the three validated ``ref_*`` tables; fail loud if any are missing."""
    paths = {
        "ref_trial": clean_root / "ref_trial.parquet",
        "ref_arm": clean_root / "ref_arm.parquet",
        "ref_withdrawal_reason": clean_root / "ref_withdrawal_reason.parquet",
    }
    missing = [str(p) for p in paths.values() if not p.exists()]
    if missing:
        raise FileNotFoundError(
            "EDA requires the cleaned ref_* tables; run the clean stage first. Missing: "
            + ", ".join(missing)
        )
    return EdaInputs(
        trials=pd.read_parquet(paths["ref_trial"]),
        arms=pd.read_parquet(paths["ref_arm"]),
        withdrawals=pd.read_parquet(paths["ref_withdrawal_reason"]),
    )


def trial_dropout(trials: pd.DataFrame, arms: pd.DataFrame) -> pd.DataFrame:
    """Trial-level participant-weighted dropout rate joined to trial covariates."""
    agg = arms.groupby("nct_id", as_index=False).agg(
        started=("started", "sum"),
        not_completed=("not_completed", "sum"),
        n_arms_flow=("arm_id", "nunique"),
    )
    agg = agg[agg["started"] > 0].copy()
    agg["dropout_rate"] = agg["not_completed"] / agg["started"]
    return trials.merge(agg, on="nct_id", how="inner")


def _enrollment_bucket(enrollment: pd.Series) -> pd.Series:
    bins = [0, 50, 100, 250, 500, 1000, np.inf]
    labels = ["1-50", "51-100", "101-250", "251-500", "501-1000", "1000+"]
    return pd.cut(enrollment, bins=bins, labels=labels, right=True)


# --- figures ---------------------------------------------------------------------------
def _save(fig: plt.Figure, name: str) -> str:
    FIG_ROOT.mkdir(parents=True, exist_ok=True)
    path = FIG_ROOT / name
    fig.savefig(path, dpi=120, bbox_inches="tight")
    plt.close(fig)
    return str(path)


def _fig_dropout_hist(td: pd.DataFrame) -> str:
    fig, ax = plt.subplots(figsize=(7, 4))
    ax.hist(td["dropout_rate"], bins=40, color="#3a6ea5", edgecolor="white")
    ax.axvline(td["dropout_rate"].mean(), color="#c0392b", ls="--", label="mean")
    ax.set_xlabel("trial dropout rate")
    ax.set_ylabel("trials")
    ax.set_title("Dropout-rate distribution (real AACT cohort)")
    ax.legend()
    return _save(fig, "dropout_rate_hist.png")


def _fig_dropout_by_cat(td: pd.DataFrame, col: str, name: str, title: str) -> str:
    grp = (
        td.groupby(col)["dropout_rate"]
        .agg(["mean", "count"])
        .sort_values("mean", ascending=False)
    )
    grp = grp[grp["count"] >= 20]  # suppress tiny strata in the figure
    fig, ax = plt.subplots(figsize=(8, max(3, 0.4 * len(grp) + 1)))
    ax.barh(grp.index.astype(str), grp["mean"], color="#2e86ab")
    ax.invert_yaxis()
    ax.set_xlabel("mean trial dropout rate")
    ax.set_title(title)
    for i, (m, c) in enumerate(zip(grp["mean"], grp["count"], strict=True)):
        ax.text(m + 0.002, i, f"n={int(c)}", va="center", fontsize=8)
    return _save(fig, name)


def _fig_enrollment_hist(td: pd.DataFrame) -> str:
    fig, ax = plt.subplots(figsize=(7, 4))
    data = td["enrollment"].clip(upper=td["enrollment"].quantile(0.99))
    ax.hist(data, bins=50, color="#5c946e", edgecolor="white")
    ax.set_xlabel("enrollment (clipped at p99)")
    ax.set_ylabel("trials")
    ax.set_title("Enrollment-size distribution (real AACT cohort)")
    return _save(fig, "enrollment_hist.png")


def _fig_reason_mix(reason_mix: pd.Series) -> str:
    fig, ax = plt.subplots(figsize=(8, 4))
    ax.bar(reason_mix.index.astype(str), reason_mix.values, color="#a8576b")
    ax.set_ylabel("share of withdrawal count")
    ax.set_title("Withdrawal-reason mix (controlled vocab, real AACT)")
    ax.tick_params(axis="x", rotation=45)
    for lbl in ax.get_xticklabels():
        lbl.set_ha("right")
    return _save(fig, "withdrawal_reason_mix.png")


def _fig_missingness(miss: pd.Series) -> str:
    miss = miss[miss > 0].sort_values(ascending=False)
    fig, ax = plt.subplots(figsize=(8, max(3, 0.4 * len(miss) + 1)))
    if len(miss):
        ax.barh(miss.index.astype(str), miss.values, color="#d08c34")
        ax.invert_yaxis()
    ax.set_xlabel("fraction missing (ref_trial)")
    ax.set_title("Per-field missingness (ref_trial)")
    return _save(fig, "missingness.png")


# --- associations ----------------------------------------------------------------------
def _covariate_associations(td: pd.DataFrame) -> list[dict[str, Any]]:
    """Sign + rough effect size of baseline covariate -> dropout associations.

    Numeric covariates: Pearson correlation with dropout rate (sign + magnitude). Effect
    size also reported as the dropout-rate gap between the top and bottom covariate tercile.
    """
    out: list[dict[str, Any]] = []
    numeric = [
        "enrollment",
        "n_arms",
        "n_sites",
        "n_countries",
        "planned_duration_days",
        "min_age_years",
        "max_age_years",
    ]
    for col in numeric:
        sub = td[[col, "dropout_rate"]].dropna()
        if len(sub) < 50 or sub[col].nunique() < 3:
            continue
        r = float(np.corrcoef(sub[col], sub["dropout_rate"])[0, 1])
        try:
            terc = pd.qcut(
                sub[col], 3, labels=["low", "mid", "high"], duplicates="drop"
            )
            gap = float(
                sub.loc[terc == "high", "dropout_rate"].mean()
                - sub.loc[terc == "low", "dropout_rate"].mean()
            )
        except ValueError:
            gap = float("nan")
        out.append(
            {
                "covariate": col,
                "pearson_r": round(r, 4),
                "sign": "positive" if r > 0 else "negative",
                "high_minus_low_tercile_dropout_gap": round(gap, 4),
                "n": int(len(sub)),
            }
        )
    # Categorical: masking (blinded vs open) effect on dropout.
    blinded = td["masking"].ne("NONE")
    out.append(
        {
            "covariate": "blinded(vs open label)",
            "blinded_mean_dropout": round(
                float(td.loc[blinded, "dropout_rate"].mean()), 4
            ),
            "open_mean_dropout": round(
                float(td.loc[~blinded, "dropout_rate"].mean()), 4
            ),
            "n": int(len(td)),
        }
    )
    return out


def _missingness(trials: pd.DataFrame) -> pd.Series:
    return trials.isna().mean().sort_values(ascending=False)


def _reason_mix(withdrawals: pd.DataFrame) -> pd.Series:
    total = withdrawals.groupby("reason")["count"].sum()
    return (total / total.sum()).sort_values(ascending=False)


def run_eda(clean_root: Path = CLEAN_ROOT, out_root: Path = EDA_ROOT) -> dict[str, Any]:
    """Compute every required EDA artifact and write figures + summary. Returns the summary."""
    data = load_clean(clean_root)
    td = trial_dropout(data.trials, data.arms)
    td = td.assign(enrollment_bucket=_enrollment_bucket(td["enrollment"]))

    reason_mix = _reason_mix(data.withdrawals)
    miss = _missingness(data.trials)

    figures = {
        "dropout_rate_hist": _fig_dropout_hist(td),
        "dropout_by_phase": _fig_dropout_by_cat(
            td, "phase", "dropout_by_phase.png", "Dropout rate by phase"
        ),
        "dropout_by_therapeutic_area": _fig_dropout_by_cat(
            td,
            "therapeutic_area",
            "dropout_by_therapeutic_area.png",
            "Dropout rate by therapeutic area",
        ),
        "dropout_by_enrollment_size": _fig_dropout_by_cat(
            td,
            "enrollment_bucket",
            "dropout_by_enrollment_size.png",
            "Dropout rate by enrollment size",
        ),
        "enrollment_hist": _fig_enrollment_hist(td),
        "withdrawal_reason_mix": _fig_reason_mix(reason_mix),
        "missingness": _fig_missingness(miss),
    }

    def _by(col: str) -> dict[str, dict[str, float]]:
        g = td.groupby(col, observed=True)["dropout_rate"].agg(["mean", "count"])
        return {
            str(k): {
                "mean_dropout": round(float(v["mean"]), 4),
                "n_trials": int(v["count"]),
            }
            for k, v in g.iterrows()
        }

    td = td.assign(
        site_band=np.where(td["n_sites"] <= 1, "SINGLE_SITE", "MULTI_SITE"),
        blinding=np.where(td["masking"].ne("NONE"), "BLINDED", "OPEN_LABEL"),
    )

    def _by_weighted(col: str) -> dict[str, dict[str, float]]:
        """Participant-weighted (sum not_completed / sum started) marginal by ``col``.

        This is the aggregation consistent with the participant-weighted OVERALL rate and
        with a participant-level synthetic cohort, so the synthetic calibration grades against
        these (the trial-mean ``dropout_by_*`` blocks are for human display).
        """
        g = td.groupby(col, observed=True).agg(
            started=("started", "sum"),
            not_completed=("not_completed", "sum"),
            n_trials=("nct_id", "nunique"),
        )
        return {
            str(k): {
                "mean_dropout": round(float(v["not_completed"] / v["started"]), 4)
                if v["started"] > 0
                else 0.0,
                "n_trials": int(v["n_trials"]),
            }
            for k, v in g.iterrows()
        }

    summary: dict[str, Any] = {
        "source": "real hosted AACT snapshot (data/clean/ref_*.parquet); build-time only",
        "study_count_post_filter": int(len(data.trials)),
        "trials_with_flow_arms": int(len(td)),
        "n_arms": int(len(data.arms)),
        "n_withdrawal_rows": int(len(data.withdrawals)),
        "overall_dropout_rate": {
            "trial_mean": round(float(td["dropout_rate"].mean()), 4),
            "trial_median": round(float(td["dropout_rate"].median()), 4),
            "participant_weighted": round(
                float(td["not_completed"].sum() / td["started"].sum()), 4
            ),
        },
        "dropout_by_phase": _by("phase"),
        "dropout_by_therapeutic_area": _by("therapeutic_area"),
        "dropout_by_enrollment_size": _by("enrollment_bucket"),
        "dropout_by_sponsor_class": _by("sponsor_class"),
        "dropout_by_site_count": _by("site_band"),
        # Participant-weighted marginals — the calibration targets (consistent with the
        # participant-weighted overall and a participant-level synthetic cohort).
        "dropout_by_phase_pw": _by_weighted("phase"),
        "dropout_by_therapeutic_area_pw": _by_weighted("therapeutic_area"),
        "dropout_by_sponsor_class_pw": _by_weighted("sponsor_class"),
        "dropout_by_blinding_pw": _by_weighted("blinding"),
        "dropout_by_site_count_pw": _by_weighted("site_band"),
        "dropout_by_enrollment_size_pw": _by_weighted("enrollment_bucket"),
        "withdrawal_reason_mix": {
            str(k): round(float(v), 4) for k, v in reason_mix.items()
        },
        "withdrawal_reason_mix_note": (
            "Shares are over ref_withdrawal_reason volume. STUDY_TERMINATED (sponsor/DSMB) "
            "and ADMINISTRATIVE (survey/data bookkeeping) are real exits but not participant-"
            "level retention events. Right-censoring / still-ongoing rows are excluded from "
            "this mix entirely (recorded in the data-quality report as excluded-censoring)."
        ),
        "enrollment_distribution": {
            q: round(float(td["enrollment"].quantile(q)), 1)
            for q in (0.1, 0.25, 0.5, 0.75, 0.9, 0.99)
        },
        "missingness_ref_trial": {
            k: round(float(v), 4) for k, v in miss.items() if v > 0
        },
        "covariate_dropout_associations": _covariate_associations(td),
        "figures": figures,
    }

    out_root.mkdir(parents=True, exist_ok=True)
    (out_root / "eda_summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True), encoding="utf-8"
    )
    (out_root / "eda_summary.md").write_text(_to_markdown(summary), encoding="utf-8")
    return summary


def _to_markdown(s: dict[str, Any]) -> str:
    lines = [
        "# Vigil EDA — real AACT reference cohort",
        "",
        f"- Source: {s['source']}",
        f"- Studies after population filter: **{s['study_count_post_filter']:,}**",
        f"- Trials with participant-flow arms: {s['trials_with_flow_arms']:,}",
        f"- Arms: {s['n_arms']:,} | Withdrawal rows: {s['n_withdrawal_rows']:,}",
        "",
        "## Overall dropout rate",
        f"- trial mean: {s['overall_dropout_rate']['trial_mean']}",
        f"- trial median: {s['overall_dropout_rate']['trial_median']}",
        f"- participant-weighted: {s['overall_dropout_rate']['participant_weighted']}",
        "",
        "## Dropout by phase",
    ]
    for k, v in sorted(s["dropout_by_phase"].items()):
        lines.append(f"- {k}: {v['mean_dropout']} (n={v['n_trials']})")
    lines += ["", "## Dropout by therapeutic area"]
    for k, v in sorted(
        s["dropout_by_therapeutic_area"].items(),
        key=lambda kv: -kv[1]["mean_dropout"],
    ):
        lines.append(f"- {k}: {v['mean_dropout']} (n={v['n_trials']})")
    lines += ["", "## Dropout by enrollment size"]
    for k, v in s["dropout_by_enrollment_size"].items():
        lines.append(f"- {k}: {v['mean_dropout']} (n={v['n_trials']})")
    lines += ["", "## Dropout by sponsor class"]
    for k, v in s["dropout_by_sponsor_class"].items():
        lines.append(f"- {k}: {v['mean_dropout']} (n={v['n_trials']})")
    lines += ["", "## Dropout by site count (single vs multi-site)"]
    for k, v in s["dropout_by_site_count"].items():
        lines.append(f"- {k}: {v['mean_dropout']} (n={v['n_trials']})")
    lines += ["", "## Withdrawal-reason mix (controlled vocab)"]
    for k, v in s["withdrawal_reason_mix"].items():
        lines.append(f"- {k}: {v}")
    lines += ["", "## Covariate -> dropout associations (sign + effect size)"]
    for a in s["covariate_dropout_associations"]:
        lines.append(f"- {a}")
    lines += ["", "## Figures"]
    for k, v in s["figures"].items():
        lines.append(f"- {k}: `{v}`")
    return "\n".join(lines) + "\n"
