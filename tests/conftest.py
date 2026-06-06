"""Shared fixtures: clean the committed GOLDEN SET into ``ref_*`` frames once per session.

The golden set (``tests/golden/``) is a frozen slice of REAL PUBLIC AACT trial-level data
committed alongside its expected cleaned output — the ingestion clean-transform oracle. The
fast suite cleans it directly (no network, no fabricated fixture, no committed ``data/``).

``clean_frames`` is the full golden cohort (~64 trials) — small enough for the fast suite, and
spanning every (phase x sponsor_class x has_withdrawal x has_max_age) stratum so the clean
transform, synthetic generation, and calibration are all meaningfully exercised.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd
import pytest

from ingestion.clean import clean_snapshot
from ingestion.config import CLEAN_ROOT, GOLDEN_RAW_ROOT
from ingestion.report import QualityReport
from ingestion.synthetic import subsample_trials


@pytest.fixture(scope="session")
def golden_raw_root() -> Path:
    """The committed golden raw snapshot (REAL public AACT slice, no PHI)."""
    if not GOLDEN_RAW_ROOT.exists():
        raise FileNotFoundError(
            f"golden raw snapshot missing at {GOLDEN_RAW_ROOT}; "
            f"run `uv run python -m tests.golden.build_golden`."
        )
    return GOLDEN_RAW_ROOT


@pytest.fixture(scope="session")
def _golden_cleaned(
    golden_raw_root: Path, tmp_path_factory
) -> tuple[dict[str, pd.DataFrame], QualityReport]:
    """Clean the golden slice once; return both the frames and the quality report."""
    out = tmp_path_factory.mktemp("golden_clean")
    report = QualityReport()
    frames = clean_snapshot(
        golden_raw_root, report=report, out_root=out, live=False, fail_loud=True
    )
    return frames, report


@pytest.fixture(scope="session")
def clean_frames(_golden_cleaned) -> dict[str, pd.DataFrame]:
    """The cleaned ``ref_*`` frames from the golden set (the fast default cohort)."""
    return _golden_cleaned[0]


@pytest.fixture(scope="session")
def golden_report(_golden_cleaned) -> QualityReport:
    """The quality report captured while cleaning the golden set."""
    return _golden_cleaned[1]


@pytest.fixture(scope="session")
def real_calibration_frames(clean_frames) -> dict[str, pd.DataFrame]:
    """The production-size stratified subsample of the REAL cleaned ``ref_*`` tables, when present.

    Calibration is only meaningful against a cohort whose stratum mix mirrors the real
    population, so this draws from ``data/clean/ref_*.parquet`` (the AACT snapshot) using the same
    default ``subsample_trials`` size the pipeline uses. When that snapshot is absent (CI /
    golden-only), it falls back to the golden frames; the slow calibration test skips.
    """
    paths = {
        name: CLEAN_ROOT / f"{name}.parquet"
        for name in ("ref_trial", "ref_arm", "ref_withdrawal_reason")
    }
    if not all(p.exists() for p in paths.values()):
        return {"_real": False, **clean_frames}
    rt = pd.read_parquet(paths["ref_trial"])
    ra = pd.read_parquet(paths["ref_arm"])
    rw = pd.read_parquet(paths["ref_withdrawal_reason"])
    if len(rt) <= len(clean_frames["ref_trial"]):
        return {"_real": False, **clean_frames}
    st, sa, sw = subsample_trials(rt, ra, rw)  # production default size
    return {
        "_real": True,
        "ref_trial": st,
        "ref_arm": sa,
        "ref_withdrawal_reason": sw,
    }
