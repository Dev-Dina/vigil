"""Synthetic determinism, labelling, and calibration."""

from __future__ import annotations

import pandas as pd
import pytest

from ingestion import SYNTHETIC_SEED
from ingestion.calibration import evaluate
from ingestion.synthetic import generate
from ingestion.targets import compute_targets


def _targets(clean_frames):
    return compute_targets(
        clean_frames["ref_trial"],
        clean_frames["ref_arm"],
        clean_frames["ref_withdrawal_reason"],
    )


def test_every_row_flagged_synthetic(clean_frames) -> None:
    targets = _targets(clean_frames)
    cohort = generate(
        clean_frames["ref_trial"], clean_frames["ref_arm"], targets, seed=SYNTHETIC_SEED
    )
    assert cohort.participants["synthetic"].all()
    assert cohort.engagement["synthetic"].all()


def test_deterministic_regeneration_is_bit_identical(clean_frames) -> None:
    targets = _targets(clean_frames)
    a = generate(
        clean_frames["ref_trial"], clean_frames["ref_arm"], targets, seed=SYNTHETIC_SEED
    )
    b = generate(
        clean_frames["ref_trial"], clean_frames["ref_arm"], targets, seed=SYNTHETIC_SEED
    )
    pd.testing.assert_frame_equal(a.participants, b.participants)
    pd.testing.assert_frame_equal(a.engagement, b.engagement)


def test_different_seed_changes_output(clean_frames) -> None:
    targets = _targets(clean_frames)
    a = generate(clean_frames["ref_trial"], clean_frames["ref_arm"], targets, seed=1)
    b = generate(clean_frames["ref_trial"], clean_frames["ref_arm"], targets, seed=2)
    assert not a.participants.equals(b.participants)


def test_calibration_all_targets_pass(clean_frames) -> None:
    targets = _targets(clean_frames)
    cohort = generate(
        clean_frames["ref_trial"], clean_frames["ref_arm"], targets, seed=SYNTHETIC_SEED
    )
    results = evaluate(cohort, targets, fail_loud=True)
    assert {r.target for r in results} == {
        "overall_dropout_rate",
        "dropout_by_stratum",
        "reason_mix",
        "dropout_timing",
        "covariate_associations",
        "enrollment_marginals",
    }
    assert all(r.passed for r in results)


@pytest.mark.slow
def test_full_cohort_regeneration_calibrates(full_clean_frames) -> None:
    """Full ~18k-participant cohort: regenerate and pass every calibration target.

    Excluded by default and from CI (run via `make test-slow`) because regenerating the
    whole sample cohort is expensive.
    """
    targets = _targets(full_clean_frames)
    cohort = generate(
        full_clean_frames["ref_trial"],
        full_clean_frames["ref_arm"],
        targets,
        seed=SYNTHETIC_SEED,
    )
    assert cohort.participants["synthetic"].all()
    results = evaluate(cohort, targets, fail_loud=True)
    assert all(r.passed for r in results)
