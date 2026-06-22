"""Phase 3 Step (a): the reproducible build-time baseline trainers.

Two baselines on the REAL cleaned ``ref_*`` modelling cohort (specs/data.md "Evaluation
contract": held-out split = temporal, group-disjoint-by-``nct_id``; **PR-AUC primary**,
calibration/Brier, lead-time gain, **reported per ``sponsor_class``**; **no rebalancing**;
scalers/encoders fit on **train only**, inherited from the Step-0 feature contract):

- a **HistGradientBoostingRegressor** on the continuous per-arm ``dropout_rate`` — it consumes
  the contract's NaN ages **natively, with no imputation** (the no-impute path);
- a **LogisticRegression** on a label thresholded at the **train-set median** dropout_rate.
  LogisticRegression cannot take NaN, so for the LINEAR model only the design matrix is
  zero-filled (``X.fillna(0.0)`` == the train mean post-standardization). This is the
  **missing-indicator method**, NOT silent value-imputation: the matrix already carries the
  explicit ``<col>_missing`` indicator columns, so missingness is preserved as a feature.

Build-time only: every artifact lands under ``MODELS_ROOT`` (``data/models/``, committed; only
``data/raw`` is git-ignored) and is never reached at runtime or by an agent. Leakage is checked LOUD before any model is fit.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")  # headless: no GUI/IPython dependency for SHAP/diagnostic plots.

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
import shap  # noqa: E402
from sklearn.ensemble import HistGradientBoostingRegressor  # noqa: E402
from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.metrics import (  # noqa: E402
    average_precision_score,
    mean_absolute_error,
    precision_recall_curve,
    r2_score,
    root_mean_squared_error,
)

from ingestion.config import CLEAN_ROOT  # noqa: E402
from models.cards.template import render_model_card  # noqa: E402
from models.cohort import cohort_nct_ids, modelling_cohort  # noqa: E402
from models.config import (  # noqa: E402
    MODEL_SEED,
    MODELLING_PHASES,
    MODELS_ROOT,
    SPONSOR_CLASS_FOLD,
)
from models.features.contract import (  # noqa: E402
    BOOLEAN_FEATURES,
    CATEGORICAL_FEATURES,
    NUMERIC_FEATURES,
    FeatureMatrix,
    assemble_rows,
    build_matrices,
)
from models.leakage_check import run_smoke  # noqa: E402
from models.plots import (  # noqa: E402
    plot_calibration_curve,
    plot_metric_by_group,
    plot_pr_curve,
    plot_residuals,
)
from models.splits import temporal_group_split  # noqa: E402

#: The reporting groups after folding the sparse ``OTHER_GOV`` class (specs/data.md
#: "Feature contract"): INDUSTRY / ACADEMIC_OTHER / NIH.
REPORT_GROUPS: tuple[str, ...] = ("INDUSTRY", "ACADEMIC_OTHER", "NIH")

#: Fixed precision floor for the recall@precision metric on the classifier.
FIXED_PRECISION: float = 0.5


# --- helpers -------------------------------------------------------------------------------
def _fold_group(sponsor_class: pd.Series) -> pd.Series:
    """Apply ``SPONSOR_CLASS_FOLD`` so reporting groups are INDUSTRY/ACADEMIC_OTHER/NIH."""
    return sponsor_class.astype("string").replace(SPONSOR_CLASS_FOLD)


def _clip01(values: np.ndarray) -> np.ndarray:
    """Clip predicted rates into [0, 1] so they are valid probabilities for calibration/PR."""
    return np.clip(np.asarray(values, dtype=float), 0.0, 1.0)


def _safe_average_precision(y_bin: np.ndarray, score: np.ndarray) -> float:
    """``average_precision_score`` that returns NaN when only one class is present."""
    if len(np.unique(y_bin)) < 2:
        return float("nan")
    return float(average_precision_score(y_bin, score))


def _recall_at_precision(
    y_bin: np.ndarray, score: np.ndarray, min_precision: float
) -> float:
    """Max recall achievable while precision >= ``min_precision`` (NaN if not computable)."""
    if len(np.unique(y_bin)) < 2:
        return float("nan")
    precision, recall, _ = precision_recall_curve(y_bin, score)
    feasible = recall[precision >= min_precision]
    return float(feasible.max()) if feasible.size else 0.0


def _brier(y_bin: np.ndarray, prob: np.ndarray) -> float:
    """Brier score against the binary high-dropout label."""
    return float(np.mean((prob - y_bin) ** 2))


def _regression_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    """MAE / RMSE / R^2 for the continuous regressor."""
    return {
        "mae": float(mean_absolute_error(y_true, y_pred)),
        "rmse": float(root_mean_squared_error(y_true, y_pred)),
        "r2": float(r2_score(y_true, y_pred)),
    }


def _gbt_metrics(
    y_true: np.ndarray, y_pred: np.ndarray, y_bin: np.ndarray
) -> dict[str, float]:
    """The GBT (regressor) metric block: regression + PR-AUC + Brier on clipped predictions."""
    prob = _clip01(y_pred)
    block = _regression_metrics(y_true, y_pred)
    block["pr_auc"] = _safe_average_precision(y_bin, prob)
    block["brier"] = _brier(y_bin, prob)
    block["recall_at_p50"] = _recall_at_precision(y_bin, prob, FIXED_PRECISION)
    return block


def _logit_metrics(y_bin: np.ndarray, proba: np.ndarray) -> dict[str, float]:
    """The logistic (classifier) metric block: PR-AUC (primary) + recall@precision + Brier."""
    return {
        "pr_auc": _safe_average_precision(y_bin, proba),
        "recall_at_p50": _recall_at_precision(y_bin, proba, FIXED_PRECISION),
        "brier": _brier(y_bin, proba),
    }


def _per_group_metrics(
    group: pd.Series, score_fn: Any, *args: np.ndarray
) -> dict[str, dict[str, float]]:
    """Compute ``score_fn`` within each (folded) sponsor_class group present in the fold."""
    folded = _fold_group(group).to_numpy()
    out: dict[str, dict[str, float]] = {}
    for name in sorted(set(folded.tolist())):
        if name not in REPORT_GROUPS:
            # Unknown / null sponsor_class — surface loudly rather than hide it.
            raise ValueError(f"unexpected sponsor_class group after folding: {name!r}")
        mask = folded == name
        out[name] = score_fn(*(arg[mask] for arg in args))
    return out


def _shap_global(
    gbt: HistGradientBoostingRegressor,
    background: pd.DataFrame,
    sample: pd.DataFrame,
    feature_names: list[str],
    out: Path,
    *,
    top_n: int = 20,
) -> tuple[Path, dict[str, float]]:
    """Global SHAP: mean(|shap|) per feature -> top-N horizontal bar PNG. Robust + headless."""
    explainer = shap.Explainer(gbt.predict, background)
    values = explainer(sample)
    mean_abs = np.abs(values.values).mean(axis=0)
    importance = dict(
        sorted(
            zip(feature_names, mean_abs.tolist(), strict=True), key=lambda kv: -kv[1]
        )
    )
    top = list(importance.items())[:top_n][::-1]
    labels = [name for name, _ in top]
    heights = [val for _, val in top]
    fig, ax = plt.subplots(figsize=(7.0, 6.0), dpi=120)
    ax.barh(labels, heights, color="#2f5f8f", edgecolor="#1f3f5f")
    ax.set(title="Global SHAP (mean |value|)", xlabel="mean(|SHAP|)")
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, format="png", bbox_inches="tight")
    plt.close(fig)
    return out, importance


def _shap_local(
    gbt: HistGradientBoostingRegressor,
    background: pd.DataFrame,
    examples: pd.DataFrame,
    out_dir: Path,
    *,
    prefix: str = "shap_local",
) -> list[Path]:
    """Local SHAP: per-example waterfall PNGs (fall back to a bar if the waterfall API trips)."""
    out_dir.mkdir(parents=True, exist_ok=True)
    explainer = shap.Explainer(gbt.predict, background)
    values = explainer(examples)
    paths: list[Path] = []
    for i in range(len(examples)):
        out = out_dir / f"{prefix}_{i}.png"
        try:
            plt.figure()
            shap.plots.waterfall(values[i], show=False)
            plt.savefig(out, format="png", bbox_inches="tight")
            plt.close()
        except Exception:  # pragma: no cover - fragile shap plot API fallback
            plt.close("all")
            contrib = values.values[i]
            order = np.argsort(-np.abs(contrib))[:20][::-1]
            fig, ax = plt.subplots(figsize=(7.0, 6.0), dpi=120)
            ax.barh(
                [examples.columns[j] for j in order],
                [float(contrib[j]) for j in order],
                color="#6f7f8f",
            )
            ax.set(title=f"Local SHAP (example {i})", xlabel="SHAP contribution")
            fig.tight_layout()
            fig.savefig(out, format="png", bbox_inches="tight")
            plt.close(fig)
        paths.append(out)
    return paths


# --- the trainer ---------------------------------------------------------------------------
def train_baselines_from_frames(
    ref_trial: pd.DataFrame,
    ref_arm: pd.DataFrame,
    *,
    out_root: Path = MODELS_ROOT,
    shap_sample: int = 300,
) -> dict[str, Any]:
    """Train + evaluate both baselines from in-memory ``ref_*`` frames; write artifacts.

    The seam ``train_baselines`` delegates to (parquet loaded once, then handed here). Does the
    full flow: cohort -> rows -> split -> matrices -> loud leakage check -> fit GBT (NaN-native,
    no impute) + logistic (missing-indicator zero-fill) -> evaluate TEST (primary) & VAL,
    per-sponsor_class -> SHAP (global + local) -> metrics.json + model_card.md + PNGs.
    """
    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    cohort = modelling_cohort(ref_trial)
    nct_ids = cohort_nct_ids(ref_trial)
    arms = ref_arm[ref_arm["nct_id"].isin(nct_ids)].copy()
    rows = assemble_rows(cohort, arms)
    split = temporal_group_split(cohort)
    matrices = build_matrices(rows, split)

    # Fail loud BEFORE any model touches the data.
    run_smoke(matrices)

    train, val, test = matrices["train"], matrices["val"], matrices["test"]
    feature_names = train.feature_names

    # Threshold = TRAIN-set median dropout_rate (computed on train only; reported).
    threshold = float(np.median(train.y.to_numpy(dtype=float)))

    # --- GBT regressor: consumes NaN natively, NO imputation, NO rebalancing -----------------
    gbt = HistGradientBoostingRegressor(random_state=MODEL_SEED)
    # Fit on the DataFrame so feature names align with the SHAP DataFrame predictions.
    gbt.fit(train.X, train.y.to_numpy(dtype=float))

    # --- Logistic classifier: missing-indicator method (zero-fill of standardized NaN) -------
    # 0.0 == the train mean post-standardization; the explicit ``_missing`` indicator columns
    # in X preserve missingness as a feature. NOT silent value-imputation. NO rebalancing.
    x_train_lin = train.X.fillna(0.0).to_numpy(dtype=float)
    y_train_bin = (train.y.to_numpy(dtype=float) > threshold).astype(int)
    logit = LogisticRegression(max_iter=1000, random_state=MODEL_SEED)
    logit.fit(x_train_lin, y_train_bin)

    def _evaluate(fm: FeatureMatrix) -> dict[str, Any]:
        y_true = fm.y.to_numpy(dtype=float)
        y_bin = (y_true > threshold).astype(int)
        # GBT
        gbt_pred = gbt.predict(fm.X)
        gbt_overall = _gbt_metrics(y_true, gbt_pred, y_bin)
        gbt_by_group = _per_group_metrics(
            fm.sponsor_class,
            lambda yt, yp, yb: _gbt_metrics(yt, yp, yb),
            y_true,
            gbt_pred,
            y_bin,
        )
        # Logistic
        proba = logit.predict_proba(fm.X.fillna(0.0).to_numpy(dtype=float))[:, 1]
        logit_overall = _logit_metrics(y_bin, proba)
        logit_by_group = _per_group_metrics(
            fm.sponsor_class,
            lambda yb, pr: _logit_metrics(yb, pr),
            y_bin,
            proba,
        )
        # Merge per-group: one {group: {metric: value}} with prefixed metric names.
        per_group: dict[str, dict[str, float]] = {}
        for grp in sorted(set(gbt_by_group) | set(logit_by_group)):
            merged: dict[str, float] = {}
            for k, v in gbt_by_group.get(grp, {}).items():
                merged[f"gbt_{k}"] = v
            for k, v in logit_by_group.get(grp, {}).items():
                merged[f"logit_{k}"] = v
            per_group[grp] = merged
        return {
            "gbt": gbt_overall,
            "logit": logit_overall,
            "per_sponsor_class": per_group,
            "_gbt_pred": gbt_pred,
            "_y_true": y_true,
            "_y_bin": y_bin,
            "_proba": proba,
        }

    test_eval = _evaluate(test)
    val_eval = _evaluate(val)

    # --- diagnostic plots (TEST) -------------------------------------------------------------
    plot_paths: dict[str, str] = {}
    gbt_prob_test = _clip01(test_eval["_gbt_pred"])
    plot_paths["pr_curve_gbt"] = str(
        plot_pr_curve(test_eval["_y_bin"], gbt_prob_test, out_root / "pr_curve_gbt.png")
    )
    plot_paths["pr_curve_logit"] = str(
        plot_pr_curve(
            test_eval["_y_bin"], test_eval["_proba"], out_root / "pr_curve_logit.png"
        )
    )
    plot_paths["calibration_gbt"] = str(
        plot_calibration_curve(
            test_eval["_y_bin"], gbt_prob_test, out_root / "calibration_gbt.png"
        )
    )
    plot_paths["calibration_logit"] = str(
        plot_calibration_curve(
            test_eval["_y_bin"], test_eval["_proba"], out_root / "calibration_logit.png"
        )
    )
    plot_paths["residuals_gbt"] = str(
        plot_residuals(
            test_eval["_y_true"], test_eval["_gbt_pred"], out_root / "residuals_gbt.png"
        )
    )
    mae_by_group = {
        grp: metrics["gbt_mae"]
        for grp, metrics in test_eval["per_sponsor_class"].items()
        if "gbt_mae" in metrics
    }
    if mae_by_group:
        plot_paths["mae_by_sponsor_class"] = str(
            plot_metric_by_group(
                mae_by_group, out_root / "mae_by_sponsor_class.png", ylabel="GBT MAE"
            )
        )

    # --- SHAP on the GBT (primary regressor) -------------------------------------------------
    rng = np.random.default_rng(MODEL_SEED)
    n_bg = min(100, len(train.X))
    bg_idx = rng.choice(len(train.X), size=n_bg, replace=False)
    background = train.X.iloc[bg_idx].reset_index(drop=True)
    n_shap = min(shap_sample, len(test.X))
    shap_sample_df = test.X.iloc[:n_shap].reset_index(drop=True)
    shap_global_path, shap_importance = _shap_global(
        gbt, background, shap_sample_df, feature_names, out_root / "shap_global.png"
    )
    plot_paths["shap_global"] = str(shap_global_path)
    # Local: the 3 highest predicted-risk test arms.
    n_local = min(3, len(test.X))
    top_risk_idx = np.argsort(-test_eval["_gbt_pred"])[:n_local]
    local_examples = test.X.iloc[top_risk_idx].reset_index(drop=True)
    shap_local_paths = _shap_local(gbt, background, local_examples, out_root)
    plot_paths["shap_local"] = [str(p) for p in shap_local_paths]

    # --- assemble metrics --------------------------------------------------------------------
    def _overall_block(ev: dict[str, Any]) -> dict[str, Any]:
        return {"gbt": ev["gbt"], "logit": ev["logit"]}

    metrics: dict[str, Any] = {
        "data_source": "REAL",
        "threshold_train_median_dropout_rate": threshold,
        "fold_sizes": split.fold_sizes,
        "n_arms": {f: int(len(matrices[f].X)) for f in ("train", "val", "test")},
        "date_ranges": {
            "train": [str(split.train_dates[0]), str(split.train_dates[1])],
            "val": [str(split.val_dates[0]), str(split.val_dates[1])],
            "test": [str(split.test_dates[0]), str(split.test_dates[1])],
        },
        "lead_time_gain": "N/A (no per-participant time series in the aggregate baseline)",
        "test": {
            **_overall_block(test_eval),
            # Base rate: a PR-AUC is only readable against chance with its positive
            # prevalence (PR-AUC of a random ranker == prevalence). Recorded so the test
            # block is self-contained (Finding 2).
            "positive_prevalence": float(test_eval["_y_bin"].mean()),
            "n_positives": int(test_eval["_y_bin"].sum()),
            "n_total": int(len(test_eval["_y_bin"])),
            "per_sponsor_class": test_eval["per_sponsor_class"],
        },
        "val": {
            **_overall_block(val_eval),
            "positive_prevalence": float(val_eval["_y_bin"].mean()),
            "n_positives": int(val_eval["_y_bin"].sum()),
            "n_total": int(len(val_eval["_y_bin"])),
            "per_sponsor_class": val_eval["per_sponsor_class"],
        },
        "shap_global_importance_top": dict(list(shap_importance.items())[:20]),
        "artifacts": plot_paths,
    }
    # Card-facing flat overall + per_sponsor_class (TEST is the primary report surface).
    card_overall = {
        "gbt_mae": test_eval["gbt"]["mae"],
        "gbt_rmse": test_eval["gbt"]["rmse"],
        "gbt_r2": test_eval["gbt"]["r2"],
        "gbt_pr_auc": test_eval["gbt"]["pr_auc"],
        "gbt_brier": test_eval["gbt"]["brier"],
        "logit_pr_auc": test_eval["logit"]["pr_auc"],
        "logit_recall_at_p50": test_eval["logit"]["recall_at_p50"],
        "logit_brier": test_eval["logit"]["brier"],
        "lead_time_gain": "N/A",
    }

    (out_root / "metrics.json").write_text(
        json.dumps(metrics, indent=2), encoding="utf-8"
    )

    # --- model card --------------------------------------------------------------------------
    n_trials = len(cohort)
    n_arms_total = sum(metrics["n_arms"].values())
    card_meta = {
        "model_name": "Vigil Phase-3 Retention Baselines (GBT regressor + Logistic classifier)",
        "data_source": (
            f"REAL ClinicalTrials.gov/AACT cleaned ref_* (modelling cohort {n_trials} trials / "
            f"{n_arms_total} arms across train+val+test). NOT synthetic."
        ),
        "model_type": (
            "HistGradientBoostingRegressor on continuous dropout_rate (NaN-native, no "
            "imputation) + LogisticRegression on a train-median-thresholded label "
            "(missing-indicator method)"
        ),
        "cohort_description": (
            f"Modelling phases {sorted(MODELLING_PHASES)}; temporal group-disjoint-by-nct_id "
            f"split — train {split.train_dates[0]}..{split.train_dates[1]}, "
            f"val {split.val_dates[0]}..{split.val_dates[1]}, "
            f"test {split.test_dates[0]}..{split.test_dates[1]}."
        ),
        "target": (
            "continuous per-arm dropout_rate (regression); thresholded at the train-median "
            f"({threshold:.4f}) for PR-AUC framing"
        ),
        "features": [
            *(f"categorical: {c}" for c in CATEGORICAL_FEATURES),
            *(f"numeric: {c}" for c in NUMERIC_FEATURES),
            *(f"boolean: {c}" for c in BOOLEAN_FEATURES),
            "min_age_years / max_age_years carry explicit _missing indicators (never imputed)",
        ],
        "metrics": {
            **card_overall,
            "per_sponsor_class": test_eval["per_sponsor_class"],
        },
        "calibration_note": (
            "GBT predicted rate clipped to [0,1] then compared to the high-dropout (>train "
            f"median {threshold:.4f}) label; Brier (GBT) = {test_eval['gbt']['brier']:.4f}, "
            f"Brier (logistic) = {test_eval['logit']['brier']:.4f}. See calibration_*.png."
        ),
        "limitations": [
            "Sponsor-level leakage is an ACCEPTED limitation: the held-out split is "
            "group-disjoint by nct_id, NOT by sponsor, so one sponsor's trials may span folds "
            "(sponsor identity is never pulled as a feature).",
            "Aggregate per-ARM dropout rates, not per-participant sequences: this is a registry "
            "baseline proving METHOD validity, never a clinical prediction.",
            "The temporal TEST window is recent and narrow versus the long train history.",
            "The linear model uses the missing-indicator method (zero-fill of standardized NaN "
            "paired with explicit _missing indicators); the GBT consumes NaN natively with NO "
            "imputation.",
            "No rebalancing is applied to either model (per the Evaluation contract).",
            "Lead-time gain is N/A for this aggregate baseline (no per-participant time series).",
        ],
    }
    card_md = render_model_card(card_meta)
    (out_root / "model_card.md").write_text(card_md, encoding="utf-8")

    # --- results dict ------------------------------------------------------------------------
    return {
        "threshold": threshold,
        "fold_sizes": split.fold_sizes,
        "n_arms": metrics["n_arms"],
        "date_ranges": metrics["date_ranges"],
        "test": {
            "gbt": test_eval["gbt"],
            "logit": test_eval["logit"],
            "per_sponsor_class": test_eval["per_sponsor_class"],
        },
        "val": {
            "gbt": val_eval["gbt"],
            "logit": val_eval["logit"],
            "per_sponsor_class": val_eval["per_sponsor_class"],
        },
        "per_sponsor_class": test_eval["per_sponsor_class"],
        "lead_time_gain": "N/A",
        "artifacts": plot_paths,
        "out_root": str(out_root),
    }


def train_baselines(
    *,
    clean_root: Path = CLEAN_ROOT,
    out_root: Path = MODELS_ROOT,
    shap_sample: int = 300,
) -> dict[str, Any]:
    """Load the REAL cleaned ``ref_*`` parquet from ``clean_root`` and train both baselines."""
    clean_root = Path(clean_root)
    ref_trial = pd.read_parquet(clean_root / "ref_trial.parquet")
    ref_arm = pd.read_parquet(clean_root / "ref_arm.parquet")
    return train_baselines_from_frames(
        ref_trial, ref_arm, out_root=out_root, shap_sample=shap_sample
    )


def _fmt(value: Any) -> str:
    return (
        f"{value:.4f}"
        if isinstance(value, float) and np.isfinite(value)
        else str(value)
    )


def main() -> int:
    """Train the baselines on REAL ``data/clean`` and print a concise summary. Returns 0."""
    results = train_baselines()
    test = results["test"]
    print("=" * 72)
    print("Vigil Phase-3 baselines — REAL data/clean")
    print("=" * 72)
    print(f"threshold (train-median dropout_rate): {_fmt(results['threshold'])}")
    print(f"fold sizes (trials): {results['fold_sizes']}")
    print(f"arms per fold: {results['n_arms']}")
    print(f"date ranges: {results['date_ranges']}")
    print("-" * 72)
    print("OVERALL (TEST):")
    g = test["gbt"]
    print(
        f"  GBT    MAE={_fmt(g['mae'])} RMSE={_fmt(g['rmse'])} R2={_fmt(g['r2'])} "
        f"PR-AUC={_fmt(g['pr_auc'])} Brier={_fmt(g['brier'])}"
    )
    le = test["logit"]
    print(
        f"  Logit  PR-AUC={_fmt(le['pr_auc'])} recall@p50={_fmt(le['recall_at_p50'])} "
        f"Brier={_fmt(le['brier'])}"
    )
    print(f"  lead-time gain: {results['lead_time_gain']}")
    print("-" * 72)
    print("PER sponsor_class (TEST):")
    for grp in sorted(test["per_sponsor_class"]):
        m = test["per_sponsor_class"][grp]
        print(
            f"  {grp:<16} GBT_MAE={_fmt(m.get('gbt_mae'))} "
            f"GBT_PR-AUC={_fmt(m.get('gbt_pr_auc'))} "
            f"Logit_PR-AUC={_fmt(m.get('logit_pr_auc'))}"
        )
    print("-" * 72)
    print(f"artifacts written under: {results['out_root']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
