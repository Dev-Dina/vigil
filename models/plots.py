"""Matplotlib helpers for build-time model diagnostics."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import matplotlib.pyplot as plt
import numpy as np
from numpy.typing import ArrayLike


def plot_pr_curve(y_true: ArrayLike, y_score: ArrayLike, out: Path) -> Path:
    """Save a precision-recall curve PNG and return its output path."""
    truth = _as_1d_float(y_true, "y_true")
    score = _as_1d_float(y_score, "y_score")
    _validate_same_length(truth, score)

    precision, recall = _precision_recall_points(truth, score)
    fig, ax = _new_figure()
    ax.plot(recall, precision, color="#2f5f8f", linewidth=2)
    ax.set(
        title="Precision-Recall Curve",
        xlabel="Recall",
        ylabel="Precision",
        xlim=(0.0, 1.0),
        ylim=(0.0, 1.05),
    )
    ax.grid(True, color="#d9d9d9", linewidth=0.8)
    return _save_png(fig, out)


def plot_calibration_curve(
    y_true: ArrayLike,
    y_prob: ArrayLike,
    out: Path,
    n_bins: int = 10,
) -> Path:
    """Save a reliability diagram PNG with the Brier score in the title."""
    if n_bins < 2:
        msg = "n_bins must be at least 2"
        raise ValueError(msg)

    truth = _as_1d_float(y_true, "y_true")
    prob = _as_1d_float(y_prob, "y_prob")
    _validate_same_length(truth, prob)

    if np.any((prob < 0.0) | (prob > 1.0)):
        msg = "y_prob must contain probabilities in [0, 1]"
        raise ValueError(msg)

    bin_centers, observed, counts = _calibration_points(truth, prob, n_bins)
    brier = float(np.mean((prob - truth) ** 2))

    fig, ax = _new_figure()
    ax.plot([0, 1], [0, 1], color="#888888", linestyle="--", linewidth=1.5)
    if np.any(counts > 0):
        ax.plot(
            bin_centers[counts > 0],
            observed[counts > 0],
            color="#2f5f8f",
            marker="o",
            linewidth=2,
        )
    ax.set(
        title=f"Calibration Curve (Brier={brier:.4f})",
        xlabel="Mean predicted probability",
        ylabel="Observed event rate",
        xlim=(0.0, 1.0),
        ylim=(0.0, 1.0),
    )
    ax.grid(True, color="#d9d9d9", linewidth=0.8)
    return _save_png(fig, out)


def plot_residuals(y_true: ArrayLike, y_pred: ArrayLike, out: Path) -> Path:
    """Save a residual scatter plot PNG for continuous regression."""
    truth = _as_1d_float(y_true, "y_true")
    pred = _as_1d_float(y_pred, "y_pred")
    _validate_same_length(truth, pred)

    residuals = pred - truth
    fig, ax = _new_figure()
    ax.axhline(0.0, color="#888888", linestyle="--", linewidth=1.5)
    ax.scatter(pred, residuals, color="#2f5f8f", alpha=0.8, s=28)
    ax.set(
        title="Residuals",
        xlabel="Predicted value",
        ylabel="Residual",
    )
    ax.grid(True, color="#d9d9d9", linewidth=0.8)
    return _save_png(fig, out)


def plot_metric_by_group(metric_by_group: dict[str, float], out: Path, ylabel: str) -> Path:
    """Save a sponsor-class metric bar chart PNG and return its output path."""
    groups = sorted(metric_by_group)
    values = [float(metric_by_group[group]) for group in groups]

    fig, ax = _new_figure()
    ax.bar(groups, values, color="#6f7f8f", edgecolor="#4a5560", linewidth=0.8)
    ax.set(title=f"{ylabel} by Sponsor Class", xlabel="Sponsor class", ylabel=ylabel)
    ax.grid(True, axis="y", color="#d9d9d9", linewidth=0.8)
    ax.tick_params(axis="x", rotation=30)
    fig.tight_layout()
    return _save_png(fig, out)


def _as_1d_float(values: ArrayLike, name: str) -> np.ndarray[Any, np.dtype[np.float64]]:
    array = np.asarray(values, dtype=float).reshape(-1)
    if array.size == 0:
        msg = f"{name} must not be empty"
        raise ValueError(msg)
    if not np.all(np.isfinite(array)):
        msg = f"{name} must contain only finite values"
        raise ValueError(msg)
    return array


def _validate_same_length(left: np.ndarray[Any, Any], right: np.ndarray[Any, Any]) -> None:
    if left.shape[0] != right.shape[0]:
        msg = "inputs must have the same length"
        raise ValueError(msg)


def _precision_recall_points(
    y_true: np.ndarray[Any, np.dtype[np.float64]],
    y_score: np.ndarray[Any, np.dtype[np.float64]],
) -> tuple[np.ndarray[Any, np.dtype[np.float64]], np.ndarray[Any, np.dtype[np.float64]]]:
    positive = y_true > 0
    positives = int(np.sum(positive))
    if positives == 0:
        return np.array([1.0, 0.0]), np.array([0.0, 1.0])

    order = np.argsort(-y_score, kind="mergesort")
    sorted_positive = positive[order]
    true_positives = np.cumsum(sorted_positive)
    false_positives = np.cumsum(~sorted_positive)

    precision = true_positives / (true_positives + false_positives)
    recall = true_positives / positives
    precision = np.concatenate(([1.0], precision))
    recall = np.concatenate(([0.0], recall))
    return precision, recall


def _calibration_points(
    y_true: np.ndarray[Any, np.dtype[np.float64]],
    y_prob: np.ndarray[Any, np.dtype[np.float64]],
    n_bins: int,
) -> tuple[
    np.ndarray[Any, np.dtype[np.float64]],
    np.ndarray[Any, np.dtype[np.float64]],
    np.ndarray[Any, np.dtype[np.int64]],
]:
    edges = np.linspace(0.0, 1.0, n_bins + 1)
    bin_ids = np.clip(np.digitize(y_prob, edges, right=False) - 1, 0, n_bins - 1)
    centers = np.zeros(n_bins, dtype=float)
    observed = np.zeros(n_bins, dtype=float)
    counts = np.zeros(n_bins, dtype=int)

    for bin_id in range(n_bins):
        in_bin = bin_ids == bin_id
        counts[bin_id] = int(np.sum(in_bin))
        if counts[bin_id] > 0:
            centers[bin_id] = float(np.mean(y_prob[in_bin]))
            observed[bin_id] = float(np.mean(y_true[in_bin]))
    return centers, observed, counts


def _new_figure() -> tuple[plt.Figure, plt.Axes]:
    fig, ax = plt.subplots(figsize=(6.0, 4.0), dpi=120)
    ax.set_facecolor("#ffffff")
    fig.patch.set_facecolor("#ffffff")
    for spine in ax.spines.values():
        spine.set_color("#666666")
    return fig, ax


def _save_png(fig: plt.Figure, out: Path) -> Path:
    output = Path(out)
    output.parent.mkdir(parents=True, exist_ok=True)
    fig.tight_layout()
    fig.savefig(output, format="png", bbox_inches="tight")
    plt.close(fig)
    return output
