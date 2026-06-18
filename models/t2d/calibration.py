"""Gate 9.7a — monotonic output calibration for the champion sequence LSTM.

The trained LSTM discriminates correctly but its raw per-decision-point sigmoid outputs are
COMPRESSED (top out near ~0.54), so the operational ``> 0.6`` HIGH band is unreachable from the
real model. We fix this with an **isotonic** (monotonic, non-parametric) calibrator that re-maps
the probability SCALE only — it preserves ranking, so discrimination (ROC-AUC / PR-AUC) is
invariant. See ``/specs/scoring.md § Output calibration``.

Honesty contract (enforced by tests):
- Fit on the held-out ``val`` fold ONLY — disjoint from the LSTM's TRAIN fold AND the reported
  TEST fold. The calibrator uses NO outcome at score time (it is a fixed ``raw -> calibrated``
  map fit once at build time).
- Monotonic => ranking preserved => AUC invariant. It re-maps probabilities; it does NOT
  manufacture discrimination.

Runtime application (``apply_calibration``) is numpy-only: the calibrator is persisted as the
isotonic threshold knots ``(x, y)`` and applied with ``np.interp`` (which reproduces
``IsotonicRegression.predict`` with ``out_of_bounds="clip"`` exactly), so scoring never needs
sklearn at runtime.
"""

from __future__ import annotations

from typing import Any, TypedDict

import numpy as np


class Calibration(TypedDict):
    """Persisted isotonic calibrator: a monotone ``raw_prob -> calibrated_prob`` knot map."""

    method: str  # "isotonic"
    fit_split: str  # "val"
    x: list[float]  # isotonic X_thresholds_ (raw probability knots, ascending)
    y: list[float]  # isotonic y_thresholds_ (calibrated probability knots, non-decreasing)


def apply_calibration(
    probs: np.ndarray | list[float], calibration: Calibration | None
) -> np.ndarray:
    """Map raw probabilities through the persisted isotonic knots (numpy-only, no sklearn).

    ``np.interp`` linearly interpolates between knots and clips to the endpoints for
    out-of-range inputs — identical to ``IsotonicRegression(out_of_bounds="clip").predict``.
    A ``None`` calibrator is the identity (an uncalibrated artifact scores exactly as before).
    """
    arr = np.asarray(probs, dtype=float)
    if calibration is None:
        return arr
    x = np.asarray(calibration["x"], dtype=float)
    y = np.asarray(calibration["y"], dtype=float)
    return np.clip(np.interp(arr, x, y), 0.0, 1.0)


def expected_calibration_error(
    y_true: np.ndarray, prob: np.ndarray, *, bins: int = 10
) -> float:
    """Equal-width-bin ECE: mean |empirical_rate - mean_prob| weighted by bin population."""
    y = np.asarray(y_true, dtype=float)
    p = np.asarray(prob, dtype=float)
    edges = np.linspace(0.0, 1.0, bins + 1)
    ece = 0.0
    for i in range(bins):
        hi_inclusive = i == bins - 1
        m = (p >= edges[i]) & (p <= edges[i + 1] if hi_inclusive else p < edges[i + 1])
        if not m.any():
            continue
        ece += (m.sum() / len(p)) * abs(y[m].mean() - p[m].mean())
    return float(ece)


def fit_isotonic_calibrator(
    model: Any,
    synth: Any,
    split: Any,
    *,
    static_enc: Any,
    seq_means: np.ndarray,
    seq_stds: np.ndarray,
    cfg: Any,
) -> tuple[Calibration, dict[str, Any]]:
    """Fit an isotonic calibrator on the VAL fold's decision points (sklearn at build time only).

    Returns ``(calibration, report)``. ``report`` carries the honest val-fold quality numbers
    (Brier / ECE raw vs calibrated) — calibration improves probability quality without touching
    ranking. The fit consumes ONLY the val fold; the caller is responsible for asserting that the
    val fold is disjoint from train + test (the split guarantees it; the artifact freezes the ids).
    """
    from sklearn.isotonic import IsotonicRegression

    from models.t2d.metrics import brier
    from models.t2d.sequence import (
        _decision_point_eval,
        _predict_steps,
        build_tensors,
    )

    val_t = build_tensors(
        synth,
        static_enc,
        split.val_ids,
        cfg=cfg,
        seq_means=seq_means,
        seq_stds=seq_stds,
    )
    val_probs = _predict_steps(model, val_t, batch_size=cfg.batch_size)
    y_val, pr_val, _ = _decision_point_eval(val_t, val_probs)

    iso = IsotonicRegression(out_of_bounds="clip", y_min=0.0, y_max=1.0)
    iso.fit(pr_val, y_val)

    calibration: Calibration = {
        "method": "isotonic",
        "fit_split": "val",
        "x": [float(v) for v in iso.X_thresholds_],
        "y": [float(v) for v in iso.y_thresholds_],
    }

    cal_val = apply_calibration(pr_val, calibration)
    report = {
        "fit_split": "val",
        "method": "isotonic",
        "n_val_decision_points": int(len(y_val)),
        "val_base_rate": float(np.mean(y_val)),
        "val_brier_raw": brier(y_val, pr_val),
        "val_brier_calibrated": brier(y_val, cal_val),
        "val_ece_raw": expected_calibration_error(y_val, pr_val),
        "val_ece_calibrated": expected_calibration_error(y_val, cal_val),
        "raw_prob_max": float(np.max(pr_val)),
        "calibrated_prob_max": float(np.max(cal_val)),
    }
    return calibration, report
