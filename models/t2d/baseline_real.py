"""TASK 1a — the REAL-T2D structural baseline (the external real floor).

Re-runs the SAME model class as the pan-indication baselines (HistGradientBoostingRegressor on
``dropout_rate`` + LogisticRegression on a train-median-thresholded label) on the 755 T2D /
2,402 real arms ONLY (with the started<20 / dropout==1 guard), using the existing arm-aggregate
feature contract and the SINGLE shared temporal split. Reports MAE/RMSE/R2, PR-AUC,
recall@fixed-precision, calibration/Brier — and per-stratum by ``(arm_type, phase)``.

Writes ``data/models/t2d/baseline_real.json`` + plots. Does NOT touch ``data/models/`` (the
pan-indication artifacts) — this is the real-data floor for the T2D slice only.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sklearn.ensemble import HistGradientBoostingRegressor  # noqa: E402
from sklearn.linear_model import LogisticRegression  # noqa: E402

from models.config import MODEL_SEED, MODELS_ROOT  # noqa: E402
from models.features.contract import (  # noqa: E402
    FeatureMatrix,
    assemble_rows,
    build_matrices,
)
from models.leakage_check import run_smoke  # noqa: E402
from models.plots import (  # noqa: E402
    plot_calibration_curve,
    plot_pr_curve,
    plot_residuals,
)
from models.t2d.cohort import T2DCohort  # noqa: E402
from models.t2d.metrics import (  # noqa: E402
    classifier_panel,
    clip01,
    per_stratum,
    regression_metrics,
)

T2D_ROOT = MODELS_ROOT / "t2d"


def _strata(
    arms: pd.DataFrame, trials: pd.DataFrame, arm_ids: pd.Series
) -> dict[str, np.ndarray]:
    """Look up (arm_type, phase) per evaluated arm, aligned to the matrix's arm_id order.

    ``arm_type`` lives on the arm; ``phase`` lives on the trial — join via ``nct_id``.
    """
    lut = arms.merge(trials[["nct_id", "phase"]], on="nct_id", how="left").set_index(
        "arm_id"
    )
    arm_type = lut.reindex(arm_ids)["arm_type"].to_numpy()
    phase = lut.reindex(arm_ids)["phase"].to_numpy()
    return {"arm_type": arm_type, "phase": phase}


def train_real_baseline(
    cohort: T2DCohort,
    *,
    out_root: Path = T2D_ROOT,
) -> dict[str, Any]:
    """Train + evaluate the real-T2D floor; write ``baseline_real.json`` + plots."""
    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    # arm rows carry phase + arm_type already (both are contract feature columns).
    rows = assemble_rows(cohort.trials, cohort.arms)
    matrices = build_matrices(rows, cohort.split)
    run_smoke(matrices)  # fail loud BEFORE any fit

    train, val, test = matrices["train"], matrices["val"], matrices["test"]
    threshold = float(np.median(train.y.to_numpy(dtype=float)))

    # GBT regressor — NaN-native, NO imputation, NO rebalancing.
    gbt = HistGradientBoostingRegressor(random_state=MODEL_SEED)
    gbt.fit(train.X, train.y.to_numpy(dtype=float))

    # Logistic — missing-indicator method (zero-fill of standardized NaN). NO rebalancing.
    x_train_lin = train.X.fillna(0.0).to_numpy(dtype=float)
    y_train_bin = (train.y.to_numpy(dtype=float) > threshold).astype(int)
    logit = LogisticRegression(max_iter=1000, random_state=MODEL_SEED)
    logit.fit(x_train_lin, y_train_bin)

    def _evaluate(fm: FeatureMatrix) -> dict[str, Any]:
        y_true = fm.y.to_numpy(dtype=float)
        y_bin = (y_true > threshold).astype(int)
        gbt_pred = gbt.predict(fm.X)
        gbt_prob = clip01(gbt_pred)
        proba = logit.predict_proba(fm.X.fillna(0.0).to_numpy(dtype=float))[:, 1]
        strata = _strata(cohort.arms, cohort.trials, fm.arm_id)
        return {
            "gbt": {
                **regression_metrics(y_true, gbt_pred),
                **classifier_panel(y_bin, gbt_prob),
            },
            "logit": classifier_panel(y_bin, proba),
            "per_stratum_gbt": per_stratum(strata, y_bin, gbt_prob),
            "per_stratum_logit": per_stratum(strata, y_bin, proba),
            "_y_bin": y_bin,
            "_y_true": y_true,
            "_gbt_pred": gbt_pred,
            "_gbt_prob": gbt_prob,
            "_proba": proba,
        }

    test_eval = _evaluate(test)
    val_eval = _evaluate(val)

    plot_paths: dict[str, str] = {}
    plot_paths["pr_curve_real_gbt"] = str(
        plot_pr_curve(
            test_eval["_y_bin"], test_eval["_gbt_prob"], out_root / "pr_real_gbt.png"
        )
    )
    plot_paths["pr_curve_real_logit"] = str(
        plot_pr_curve(
            test_eval["_y_bin"], test_eval["_proba"], out_root / "pr_real_logit.png"
        )
    )
    plot_paths["calibration_real_gbt"] = str(
        plot_calibration_curve(
            test_eval["_y_bin"], test_eval["_gbt_prob"], out_root / "calib_real_gbt.png"
        )
    )
    plot_paths["residuals_real_gbt"] = str(
        plot_residuals(
            test_eval["_y_true"],
            test_eval["_gbt_pred"],
            out_root / "resid_real_gbt.png",
        )
    )

    metrics: dict[str, Any] = {
        "data_source": "REAL_T2D",
        "task": "1a_real_structural_floor",
        "model_class": "HistGradientBoostingRegressor + LogisticRegression (train-median label)",
        "n_trials": int(cohort.trials["nct_id"].nunique()),
        "n_arms_guarded": int(len(cohort.arms)),
        "threshold_train_median_dropout_rate": threshold,
        "fold_sizes": cohort.split.fold_sizes,
        "n_arms_per_fold": {
            f: int(len(matrices[f].X)) for f in ("train", "val", "test")
        },
        "date_ranges": {
            "train": [
                str(cohort.split.train_dates[0]),
                str(cohort.split.train_dates[1]),
            ],
            "val": [str(cohort.split.val_dates[0]), str(cohort.split.val_dates[1])],
            "test": [str(cohort.split.test_dates[0]), str(cohort.split.test_dates[1])],
        },
        "test": {
            "gbt": test_eval["gbt"],
            "logit": test_eval["logit"],
            "per_stratum_gbt": test_eval["per_stratum_gbt"],
            "per_stratum_logit": test_eval["per_stratum_logit"],
        },
        "val": {"gbt": val_eval["gbt"], "logit": val_eval["logit"]},
        "artifacts": plot_paths,
    }
    (out_root / "baseline_real.json").write_text(
        json.dumps(metrics, indent=2), encoding="utf-8"
    )
    return metrics
