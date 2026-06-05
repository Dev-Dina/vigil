"""Shared fixtures: clean the SAMPLE AACT fixture into ``ref_*`` frames once per session.

The SAMPLE fixture is GENERATED into a session tmp dir at test time (deterministic seed),
never read from a committed copy — so the suite and CI pass even when
``ingestion/fixtures/aact_sample/`` does not exist on disk.

The default ``clean_frames`` is a SMALL deterministic subset of trials so the suite runs
fast. The full cohort (every trial in the sample fixture) is exposed via
``full_clean_frames`` and exercised only by ``@pytest.mark.slow`` tests, which regenerate
the full synthetic cohort and are excluded by default and from CI.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from ingestion.clean import clean_snapshot
from ingestion.fixtures.build_sample import build as build_sample_fixture
from ingestion.report import QualityReport

# Number of trials kept in the default (fast) fixture. Small enough that synthetic
# generation + feature build is quick, large enough to span multiple strata so calibration
# remains meaningful and deterministic.
DEFAULT_TRIAL_LIMIT = 20


def _subset_frames(
    frames: dict[str, pd.DataFrame], n_trials: int
) -> dict[str, pd.DataFrame]:
    """Keep the first ``n_trials`` NCTs (sorted) across all three ref tables."""
    kept = sorted(frames["ref_trial"]["nct_id"].unique())[:n_trials]
    keep = set(kept)
    return {
        name: df[df["nct_id"].isin(keep)].reset_index(drop=True)
        for name, df in frames.items()
    }


@pytest.fixture(scope="session")
def sample_fixture_root(tmp_path_factory) -> Path:
    """Deterministically build the SAMPLE AACT fixture into a session tmp dir.

    Independent of any committed fixture: ``make data-fixture`` writes the same bytes to
    ``ingestion/fixtures/aact_sample/`` for local pipeline runs, but tests never require it.
    """
    return build_sample_fixture(out_dir=tmp_path_factory.mktemp("aact_sample"))


@pytest.fixture(scope="session")
def full_clean_frames(
    tmp_path_factory, sample_fixture_root: Path
) -> dict[str, pd.DataFrame]:
    """Every trial in the SAMPLE fixture (the full ~18k-participant cohort source)."""
    out = tmp_path_factory.mktemp("clean_full")
    report = QualityReport()
    return clean_snapshot(
        sample_fixture_root, report=report, fail_loud=True, out_root=out
    )


@pytest.fixture(scope="session")
def clean_frames(full_clean_frames) -> dict[str, pd.DataFrame]:
    """Small deterministic subset for the fast default suite."""
    return _subset_frames(full_clean_frames, DEFAULT_TRIAL_LIMIT)
