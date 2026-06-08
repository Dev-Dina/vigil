"""Synthetic determinism, labelling, and calibration."""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from ingestion import SYNTHETIC_SEED
from ingestion.calibration import evaluate
from ingestion.synthetic import generate
from ingestion.targets import compute_targets

# Committed golden fixture — structurally valid, synthetic values, no real AACT data.
# Keeps CI hermetic: tests never read data/eda/ (gitignored build-time artifact).
_EDA_FIXTURE = Path(__file__).parent / "golden" / "eda_summary_fixture.json"


def _targets(clean_frames):
    return compute_targets(
        clean_frames["ref_trial"],
        clean_frames["ref_arm"],
        clean_frames["ref_withdrawal_reason"],
        eda_path=_EDA_FIXTURE,
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


_EXPECTED_TARGETS = {
    "overall_dropout_rate",
    "dropout_by_phase",
    "dropout_by_therapeutic_area",
    "dropout_by_sponsor_class",
    "dropout_by_blinding",
    "dropout_by_site_count",
    "reason_mix",
    "dropout_timing",
    "covariate_associations",
    "enrollment_marginals",
}


def test_calibration_target_set(clean_frames) -> None:
    """The calibration grades exactly the spec's target families (names, not pass/fail)."""
    targets = _targets(clean_frames)
    cohort = generate(
        clean_frames["ref_trial"], clean_frames["ref_arm"], targets, seed=SYNTHETIC_SEED
    )
    results = evaluate(cohort, targets, fail_loud=False)
    assert {r.target for r in results} == _EXPECTED_TARGETS


@pytest.mark.slow
def test_calibration_passes_on_real_subsample(real_calibration_frames) -> None:
    """Against a real-population-representative cohort, every calibration target passes.

    Calibration is only meaningful when the cohort's stratum mix mirrors the real population
    (the synthetic cohort reproduces the real TRIAL-MEAN marginals), which needs the full
    production-size stratified subsample — slow, so excluded from the default suite and CI (run
    via `make test-slow`). When the real snapshot is absent (fixture-only), this skips.
    """
    if not real_calibration_frames.get("_real"):
        pytest.skip(
            "real cleaned ref_* snapshot not present; calibration not graded here"
        )
    targets = _targets(real_calibration_frames)
    cohort = generate(
        real_calibration_frames["ref_trial"],
        real_calibration_frames["ref_arm"],
        targets,
        seed=SYNTHETIC_SEED,
    )
    assert cohort.participants["synthetic"].all()
    results = evaluate(cohort, targets, fail_loud=True)
    assert all(r.passed for r in results)
