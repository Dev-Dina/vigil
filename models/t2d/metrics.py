"""Shared metric primitives for the T2D layer (the fixed evaluation panel).

NO single headline metric: PR-AUC (primary), recall@fixed-precision, calibration/Brier — and,
for the sequence model, lead-time gain. Per-stratum reporting is by ``(arm_type, phase)`` here
(the T2D task's strata), in addition to the contract's ``sponsor_class`` where relevant.
"""

from __future__ import annotations

import numpy as np
from sklearn.metrics import (
    average_precision_score,
    mean_absolute_error,
    precision_recall_curve,
    r2_score,
    root_mean_squared_error,
)

#: Precision floor for the recall@precision metric (shared with the pan-indication baselines).
FIXED_PRECISION: float = 0.5


def clip01(values: np.ndarray) -> np.ndarray:
    """Clip into [0, 1] so predictions are valid probabilities for PR / calibration."""
    return np.clip(np.asarray(values, dtype=float), 0.0, 1.0)


def safe_pr_auc(y_bin: np.ndarray, score: np.ndarray) -> float:
    """``average_precision_score`` returning NaN when a single class is present."""
    if len(np.unique(y_bin)) < 2:
        return float("nan")
    return float(average_precision_score(y_bin, score))


def recall_at_precision(
    y_bin: np.ndarray, score: np.ndarray, min_precision: float = FIXED_PRECISION
) -> float:
    """Max recall achievable while precision >= ``min_precision`` (NaN if not computable)."""
    if len(np.unique(y_bin)) < 2:
        return float("nan")
    precision, recall, _ = precision_recall_curve(y_bin, score)
    feasible = recall[precision >= min_precision]
    return float(feasible.max()) if feasible.size else 0.0


def brier(y_bin: np.ndarray, prob: np.ndarray) -> float:
    """Brier score against the binary label."""
    return float(np.mean((prob - np.asarray(y_bin, dtype=float)) ** 2))


def regression_metrics(y_true: np.ndarray, y_pred: np.ndarray) -> dict[str, float]:
    """MAE / RMSE / R^2 for the continuous regressor."""
    return {
        "mae": float(mean_absolute_error(y_true, y_pred)),
        "rmse": float(root_mean_squared_error(y_true, y_pred)),
        "r2": float(r2_score(y_true, y_pred)),
    }


def classifier_panel(y_bin: np.ndarray, prob: np.ndarray) -> dict[str, float]:
    """The fixed classifier panel: PR-AUC (primary), recall@p, Brier."""
    return {
        "pr_auc": safe_pr_auc(y_bin, prob),
        "recall_at_p50": recall_at_precision(y_bin, prob),
        "brier": brier(y_bin, prob),
        "n": int(len(y_bin)),
        "positives": int(np.sum(np.asarray(y_bin) > 0)),
    }


def per_stratum(
    strata: dict[str, np.ndarray], y_bin: np.ndarray, prob: np.ndarray
) -> dict[str, dict[str, dict[str, float]]]:
    """Compute the classifier panel within each level of each named stratum.

    ``strata`` maps a stratum name (e.g. ``"arm_type"``) to a per-sample label array. Returns
    ``{stratum_name: {level: panel}}``. A level with no samples is omitted (never invented).
    """
    out: dict[str, dict[str, dict[str, float]]] = {}
    for name, labels in strata.items():
        labels = np.asarray(labels)
        levels: dict[str, dict[str, float]] = {}
        for level in sorted({str(v) for v in labels.tolist()}):
            mask = labels.astype(str) == level
            if not mask.any():
                continue
            levels[level] = classifier_panel(y_bin[mask], prob[mask])
        out[name] = levels
    return out
