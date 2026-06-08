"""Feature-pipeline leakage checks (specs/data.md, non-negotiable)."""

from __future__ import annotations

from pathlib import Path

import pytest

from ingestion import SYNTHETIC_SEED
from ingestion.errors import LeakageError
from ingestion.features import (
    assert_no_leakage,
    build_features,
    fit_scaler_on_train,
    synthetic_group_split_by_trial,
)
from ingestion.synthetic import generate
from ingestion.targets import compute_targets

# Committed golden fixture — structurally valid, synthetic values, no real AACT data.
# Keeps CI hermetic: tests never read data/eda/ (gitignored build-time artifact).
_EDA_FIXTURE = Path(__file__).parent / "golden" / "eda_summary_fixture.json"


def _cohort(clean_frames):
    targets = compute_targets(
        clean_frames["ref_trial"],
        clean_frames["ref_arm"],
        clean_frames["ref_withdrawal_reason"],
        eda_path=_EDA_FIXTURE,
    )
    return generate(
        clean_frames["ref_trial"], clean_frames["ref_arm"], targets, seed=SYNTHETIC_SEED
    )


def test_no_feature_after_decision_time(clean_frames) -> None:
    cohort = _cohort(clean_frames)
    fm = build_features(cohort.participants, cohort.engagement)
    assert (fm.X["max_feature_day"] < fm.X["t"]).all()


def test_no_outcome_derived_feature(clean_frames) -> None:
    cohort = _cohort(clean_frames)
    fm = build_features(cohort.participants, cohort.engagement)
    for col in fm.feature_columns:
        name = col.split("__")[-1]
        assert name not in {"dropped", "time_to_event_days", "censored"}
        assert "dropout" not in name


def test_group_split_no_trial_overlap_and_passes(clean_frames) -> None:
    cohort = _cohort(clean_frames)
    fm = build_features(cohort.participants, cohort.engagement)
    splits = synthetic_group_split_by_trial(fm.X, seed=SYNTHETIC_SEED)
    train = set(splits["train"]["nct_id"])
    val = set(splits["val"]["nct_id"])
    test = set(splits["test"]["nct_id"])
    assert train.isdisjoint(val)
    assert train.isdisjoint(test)
    assert val.isdisjoint(test)
    fit_scaler_on_train(splits, fm.feature_columns)
    assert_no_leakage(fm, splits)  # must not raise


def test_leakage_check_catches_future_feature(clean_frames) -> None:
    cohort = _cohort(clean_frames)
    fm = build_features(cohort.participants, cohort.engagement)
    splits = synthetic_group_split_by_trial(fm.X, seed=SYNTHETIC_SEED)
    # Inject a future observation: push one sample's max feature day to >= t.
    fm.X.loc[fm.X.index[0], "max_feature_day"] = int(fm.X.loc[fm.X.index[0], "t"])
    with pytest.raises(LeakageError, match="future"):
        assert_no_leakage(fm, splits)


def test_leakage_check_catches_trial_overlap(clean_frames) -> None:
    cohort = _cohort(clean_frames)
    fm = build_features(cohort.participants, cohort.engagement)
    splits = synthetic_group_split_by_trial(fm.X, seed=SYNTHETIC_SEED)
    # Force a trial into both train and test.
    shared = splits["train"]["nct_id"].iloc[0]
    splits["test"] = splits["test"].copy()
    splits["test"].loc[splits["test"].index[:1], "nct_id"] = shared
    with pytest.raises(LeakageError, match="splits"):
        assert_no_leakage(fm, splits)
