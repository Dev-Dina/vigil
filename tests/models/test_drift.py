"""Gate M1 — PSI + KS drift statistics must be CORRECT, not approximate placeholders.

Asserts the math against hand-computed / scipy-reference values: a no-drift case (identical
distributions → PSI≈0, KS D=0 / p=1, nothing breached) and a clear-drift case (disjoint shifted
distributions → PSI>0.2 breached, KS p<0.05 breached), plus an exact hand-computed 2-bin PSI and
the KS critical-value formula. Pure functions, no DB — fast.
"""

from __future__ import annotations

import math

import numpy as np
import pytest

from models.drift import (
    KS_ALPHA,
    PSI_SIGNIFICANT,
    evaluate_drift,
    ks_critical_value,
    ks_two_sample,
    population_stability_index,
)


# --- no drift: identical distributions ------------------------------------------------------
def test_identical_distributions_no_drift() -> None:
    rng = np.random.default_rng(0)
    x = rng.normal(0.5, 0.1, size=500)
    # PSI of a sample against ITSELF is exactly 0 (proportions match bin-for-bin).
    assert population_stability_index(x, x, bins=10) == pytest.approx(0.0, abs=1e-12)
    d, p = ks_two_sample(x, x)
    assert d == pytest.approx(0.0, abs=1e-12)
    assert p == pytest.approx(1.0, abs=1e-9)

    results = {r.metric: r for r in evaluate_drift(x, x)}
    assert not results["psi"].breached, "identical → PSI must not breach"
    assert not results["ks"].breached, "identical → KS must not breach"
    assert results["psi"].value == pytest.approx(0.0, abs=1e-12)
    # KS detail carries the exact p-value (≈1.0 for identical).
    assert results["ks"].detail["p_value"] == pytest.approx(1.0, abs=1e-9)


# --- exact hand-computed PSI (2 equal-frequency bins) ---------------------------------------
def test_psi_exact_hand_computed_two_bins() -> None:
    # reference: 50/50 across the 0.5 edge; current: 25/75. With 2 reference-quantile bins the
    # edges are [-inf, 0.5, inf]. ref_prop=[.5,.5], cur_prop=[.25,.75]:
    #   PSI = (.25-.5)*ln(.25/.5) + (.75-.5)*ln(.75/.5)
    #       = -.25*ln(.5) + .25*ln(1.5) = .173287 + .101366 = .274653
    reference = [0, 0, 0, 0, 1, 1, 1, 1]
    current = [0, 0, 1, 1, 1, 1, 1, 1]
    expected = -0.25 * math.log(0.5) + 0.25 * math.log(1.5)
    psi = population_stability_index(reference, current, bins=2)
    assert psi == pytest.approx(expected, rel=1e-9)
    assert psi == pytest.approx(0.274653, abs=1e-5)
    assert psi > PSI_SIGNIFICANT  # this hand case is a significant shift


# --- clear drift: disjoint shifted distributions --------------------------------------------
def test_clear_drift_breaches_both() -> None:
    rng = np.random.default_rng(7)
    reference = rng.uniform(0.0, 0.3, size=400)  # low band
    current = rng.uniform(0.6, 1.0, size=400)  # shifted high band (disjoint)

    psi = population_stability_index(reference, current, bins=10)
    assert psi > PSI_SIGNIFICANT, f"disjoint shift must breach PSI>0.2, got {psi}"

    d, p = ks_two_sample(reference, current)
    assert d == pytest.approx(1.0, abs=1e-9), "disjoint supports → KS statistic = 1.0"
    assert p < KS_ALPHA, f"disjoint shift must breach KS (p<0.05), got p={p}"

    results = {r.metric: r for r in evaluate_drift(reference, current)}
    assert results["psi"].breached and results["ks"].breached
    # breached ⇔ p < alpha (the threshold-on-statistic and the p-value agree).
    assert results["ks"].breached == (results["ks"].detail["p_value"] < KS_ALPHA)


# --- KS critical value formula --------------------------------------------------------------
def test_ks_critical_value_formula() -> None:
    # D_crit = c(0.05) * sqrt((n+m)/(n*m)), c(0.05)=1.358.
    n = m = 50
    expected = 1.358 * math.sqrt((n + m) / (n * m))
    assert ks_critical_value(n, m, alpha=0.05) == pytest.approx(expected, rel=1e-9)
    # breached-on-statistic agrees with breached-on-pvalue for a moderate real shift.
    rng = np.random.default_rng(3)
    ref = rng.normal(0.4, 0.1, size=300)
    cur = rng.normal(0.55, 0.1, size=300)
    d, p = ks_two_sample(ref, cur)
    assert (d > ks_critical_value(300, 300)) == (p < KS_ALPHA)
