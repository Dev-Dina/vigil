"""Distribution-drift statistics — PSI + two-sample KS (Gate M1).

PURE functions over two 1-D samples (a REFERENCE window and a CURRENT window). No DB, no app
imports — so the math is unit-testable in isolation (tests/models/test_drift.py) against
hand-computed / scipy-reference values. The producer job (vigil/workers/tasks.compute_drift)
assembles the windows from real data and calls :func:`evaluate_drift`; this module only computes.

Two complementary detectors, each emitting a uniform ``DriftResult`` (value / threshold / breached
where **breached ⇔ value > threshold**, so the surface reads the same way for both):

- **PSI** (Population Stability Index): binned relative-entropy-style divergence. Standard reading:
  ``< 0.1`` no shift, ``0.1–0.2`` moderate, ``> 0.2`` significant. Bins are REFERENCE quantiles
  (equal-frequency), with ±inf outer edges so current-window tail values always land in a bin; an
  epsilon floor avoids ``log(0)`` on an empty bin.
- **KS** (two-sample Kolmogorov–Smirnov): ``scipy.stats.ks_2samp`` gives the statistic D (max CDF
  gap) and its p-value. We breach on the standard α=0.05 critical value
  ``D_crit = c(α)·sqrt((n+m)/(n·m))`` (c(0.05)=1.358) — i.e. ``D > D_crit`` ⇔ ``p < 0.05`` — so KS
  also reports value=D, threshold=D_crit, breached, with the exact p-value carried in ``detail``.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np

# Standard operating thresholds (industry-conventional; cited in specs/observability.md § Drift).
PSI_SIGNIFICANT = 0.2
KS_ALPHA = 0.05
# Kolmogorov coefficient c(α) for the two-sample critical value at α (Smirnov). c(0.05)=1.358.
_KS_C_ALPHA: dict[float, float] = {0.10: 1.224, 0.05: 1.358, 0.025: 1.480, 0.01: 1.628}


@dataclass(frozen=True, slots=True)
class DriftResult:
    """One drift metric over the (reference, current) pair. ``breached ⇔ value > threshold``."""

    metric: str  # "psi" | "ks"
    value: float
    threshold: float
    breached: bool
    reference_n: int
    current_n: int
    detail: dict[str, Any] = field(default_factory=dict)


def population_stability_index(
    reference: np.ndarray | list[float],
    current: np.ndarray | list[float],
    *,
    bins: int = 10,
    epsilon: float = 1e-6,
) -> float:
    """PSI between a reference and current sample using equal-frequency REFERENCE bins.

    ``PSI = Σ_i (c_i − r_i) · ln(c_i / r_i)`` over bins i, where r_i/c_i are the reference/current
    proportions in bin i. Bin edges are the reference quantiles (``bins+1`` edges) with the outer
    edges replaced by ±inf so every current value falls in a bin; duplicate edges (a degenerate
    reference) collapse via ``np.unique``. Identical samples → ``PSI = 0``.
    """
    ref = np.asarray(reference, dtype=float)
    cur = np.asarray(current, dtype=float)
    if ref.size == 0 or cur.size == 0:
        raise ValueError("PSI requires non-empty reference and current samples")

    edges = np.quantile(ref, np.linspace(0.0, 1.0, bins + 1))
    edges[0], edges[-1] = -np.inf, np.inf
    edges = np.unique(edges)  # collapse degenerate (repeated) edges

    ref_counts, _ = np.histogram(ref, bins=edges)
    cur_counts, _ = np.histogram(cur, bins=edges)
    ref_prop = np.clip(ref_counts / ref_counts.sum(), epsilon, None)
    cur_prop = np.clip(cur_counts / cur_counts.sum(), epsilon, None)
    return float(np.sum((cur_prop - ref_prop) * np.log(cur_prop / ref_prop)))


def ks_two_sample(
    reference: np.ndarray | list[float],
    current: np.ndarray | list[float],
) -> tuple[float, float]:
    """Two-sample KS — returns ``(statistic D, p_value)`` via ``scipy.stats.ks_2samp``."""
    from scipy.stats import ks_2samp

    res = ks_2samp(np.asarray(reference, dtype=float), np.asarray(current, dtype=float))
    return float(res.statistic), float(res.pvalue)


def ks_critical_value(n: int, m: int, *, alpha: float = KS_ALPHA) -> float:
    """Two-sample KS critical value ``D_crit = c(α)·sqrt((n+m)/(n·m))`` (reject H0 when D > D_crit)."""
    if n <= 0 or m <= 0:
        raise ValueError("KS critical value requires positive sample sizes")
    c = _KS_C_ALPHA.get(round(alpha, 3))
    if c is None:
        raise ValueError(
            f"unsupported KS alpha {alpha!r}; known: {sorted(_KS_C_ALPHA)}"
        )
    return float(c * np.sqrt((n + m) / (n * m)))


def evaluate_drift(
    reference: np.ndarray | list[float],
    current: np.ndarray | list[float],
    *,
    bins: int = 10,
    psi_threshold: float = PSI_SIGNIFICANT,
    ks_alpha: float = KS_ALPHA,
) -> list[DriftResult]:
    """Compute PSI + KS for one (reference, current) pair → two uniform ``DriftResult``s.

    PSI: ``value=psi, threshold=psi_threshold, breached = psi > threshold``.
    KS:  ``value=D, threshold=D_crit(n,m,α), breached = D > D_crit`` (⇔ p < α); ``detail`` carries
    the exact ``p_value`` + ``alpha`` so the p-value reading is never lost.
    """
    ref = np.asarray(reference, dtype=float)
    cur = np.asarray(current, dtype=float)
    n, m = int(ref.size), int(cur.size)

    psi = population_stability_index(ref, cur, bins=bins)
    d_stat, p_value = ks_two_sample(ref, cur)
    d_crit = ks_critical_value(n, m, alpha=ks_alpha)

    return [
        DriftResult(
            metric="psi",
            value=psi,
            threshold=psi_threshold,
            breached=psi > psi_threshold,
            reference_n=n,
            current_n=m,
            detail={"bins": bins},
        ),
        DriftResult(
            metric="ks",
            value=d_stat,
            threshold=d_crit,
            breached=d_stat > d_crit,
            reference_n=n,
            current_n=m,
            detail={"p_value": p_value, "alpha": ks_alpha},
        ),
    ]
