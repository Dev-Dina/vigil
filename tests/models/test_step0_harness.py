"""Step 0 harness smoke tests — cohort, temporal split, feature contract, leakage check.

Runs on the committed GOLDEN cohort (``clean_frames``), whose 64 trials are all in the four
modelling phases and now carry ``start_date`` — so the harness is exercised end-to-end with
NO ``data/clean`` dependency (CI-safe).
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from ingestion.errors import LeakageError
from models.cohort import cohort_nct_ids, modelling_cohort
from models.config import MODELLING_PHASES
from models.features.contract import assemble_rows, build_matrices
from models.leakage_check import assert_group_disjoint, assert_no_outcome_features, run_smoke
from models.splits import temporal_group_split


@pytest.fixture(scope="module")
def cohort(clean_frames) -> pd.DataFrame:
    return modelling_cohort(clean_frames["ref_trial"])


@pytest.fixture(scope="module")
def matrices(clean_frames, cohort):
    rows = assemble_rows(cohort, clean_frames["ref_arm"])
    split = temporal_group_split(cohort)
    return build_matrices(rows, split)


# --- cohort --------------------------------------------------------------------------------
def test_cohort_is_all_modelling_phases(clean_frames, cohort) -> None:
    assert set(cohort["phase"]).issubset(MODELLING_PHASES)
    # Every golden trial is a modelling-phase trial.
    assert len(cohort) == len(clean_frames["ref_trial"])
    assert len(cohort_nct_ids(clean_frames["ref_trial"])) == len(cohort)


def test_cohort_fails_loud_on_null_start_date(clean_frames) -> None:
    bad = clean_frames["ref_trial"].copy()
    bad.loc[bad.index[0], "start_date"] = None
    with pytest.raises(ValueError, match="start_date"):
        modelling_cohort(bad)


# --- split (temporal + group-disjoint) -----------------------------------------------------
def test_split_temporal_and_group_disjoint(cohort) -> None:
    split = temporal_group_split(cohort)
    # disjoint + complete
    assert split.train_ids.isdisjoint(split.val_ids)
    assert split.train_ids.isdisjoint(split.test_ids)
    assert split.val_ids.isdisjoint(split.test_ids)
    assert split.train_ids | split.val_ids | split.test_ids == set(cohort["nct_id"])
    # all folds non-empty
    assert all(v > 0 for v in split.fold_sizes.values())
    # strictly temporal: earlier-starting -> train, later -> test
    assert split.train_dates[1] < split.val_dates[0]
    assert split.val_dates[1] < split.test_dates[0]


def test_split_fails_loud_on_null_date(cohort) -> None:
    bad = cohort.copy()
    bad.loc[bad.index[0], "start_date"] = None
    with pytest.raises(ValueError, match="non-null"):
        temporal_group_split(bad)


# --- feature contract ----------------------------------------------------------------------
def test_max_age_missing_indicator_present(matrices) -> None:
    assert "max_age_years_missing" in matrices["train"].feature_names


def test_no_identity_outcome_or_synthetic_in_matrix(matrices) -> None:
    for fold in ("train", "val", "test"):
        names = matrices[fold].feature_names
        assert_no_outcome_features(names)  # must not raise
        for forbidden in ("nct_id", "arm_id", "study_url", "dropout_rate", "synthetic"):
            assert forbidden not in names


def test_other_gov_folded_no_standalone_column(matrices) -> None:
    # No one-hot column for the folded sparse class.
    assert not any(
        "sponsor_class" in n and "OTHER_GOV" in n for n in matrices["train"].feature_names
    )


def test_max_age_never_imputed_nan_preserved(matrices) -> None:
    # Where max_age_years was missing, the scaled value stays NaN (not filled).
    X = matrices["train"].X
    miss = X["max_age_years_missing"] > 0
    if miss.any():
        assert X.loc[miss, "max_age_years"].isna().all()


def test_scalers_fit_on_train_only(clean_frames, cohort) -> None:
    rows = assemble_rows(cohort, clean_frames["ref_arm"])
    split = temporal_group_split(cohort)
    mats = build_matrices(rows, split)
    # A fully-present numeric (enrollment) standardized on train has ~0 mean on train.
    train_enroll = mats["train"].X["enrollment"].to_numpy(dtype=float)
    assert abs(np.nanmean(train_enroll)) < 1e-6
    # val/test reuse train stats, so their mean is generally NOT ~0 (different distribution).
    # (Sanity: the transform ran without refitting — no exception, columns align.)
    assert list(mats["val"].X.columns) == list(mats["train"].X.columns)
    assert list(mats["test"].X.columns) == list(mats["train"].X.columns)


def test_target_is_dropout_rate_in_range(matrices) -> None:
    for fold in ("train", "val", "test"):
        y = matrices[fold].y
        assert ((y >= 0) & (y <= 1)).all()


# --- leakage check -------------------------------------------------------------------------
def test_run_smoke_passes(matrices) -> None:
    run_smoke(matrices)  # must not raise


def test_group_disjoint_catches_injected_overlap(matrices) -> None:
    corrupted = dict(matrices)
    test_fm = corrupted["test"]
    shared = matrices["train"].nct_id.iloc[0]
    test_fm.nct_id.iloc[0] = shared  # force a trial into both train and test
    with pytest.raises(LeakageError, match="span"):
        assert_group_disjoint(corrupted)


def test_assert_no_outcome_features_catches_leak() -> None:
    with pytest.raises(LeakageError):
        assert_no_outcome_features(["phase=PHASE2", "dropout_rate"])
