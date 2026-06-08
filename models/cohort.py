"""The modelling cohort, materialized ONCE and shared by baselines / synthetic / split.

A trial is in the modelling cohort iff its ``ref_trial.phase`` is one of
:data:`models.config.MODELLING_PHASES`. Reference-only phases stay in ``ref_*`` but never
contribute a training row, a synthetic participant, or a calibration statistic. The cohort
carries ``start_date`` (the temporal split axis), which must be non-null — fail loud otherwise.
"""

from __future__ import annotations

from pathlib import Path

import pandas as pd

from ingestion.config import CLEAN_ROOT
from models.config import MODELLING_PHASES

_REQUIRED = ("nct_id", "phase", "start_date")


def modelling_cohort(ref_trial: pd.DataFrame) -> pd.DataFrame:
    """Filter ``ref_trial`` to the modelling cohort; fail loud on emptiness or null dates."""
    missing = [c for c in _REQUIRED if c not in ref_trial.columns]
    if missing:
        raise ValueError(f"ref_trial is missing required columns {missing}")
    cohort = ref_trial[ref_trial["phase"].isin(MODELLING_PHASES)].copy()
    if cohort.empty:
        raise ValueError(
            f"modelling cohort is empty — no trials in phases {sorted(MODELLING_PHASES)}"
        )
    null_dates = int(cohort["start_date"].isna().sum())
    if null_dates:
        raise ValueError(
            f"{null_dates} modelling-cohort trial(s) have a null start_date; the temporal "
            f"split axis must be non-null (specs/data.md ref_trial.start_date)."
        )
    return cohort.reset_index(drop=True)


def cohort_nct_ids(ref_trial: pd.DataFrame) -> frozenset[str]:
    """The canonical modelling-cohort ``nct_id`` set."""
    return frozenset(modelling_cohort(ref_trial)["nct_id"])


def load_modelling_cohort(clean_root: Path = CLEAN_ROOT) -> pd.DataFrame:
    """Read ``ref_trial.parquet`` from ``clean_root`` and materialize the modelling cohort."""
    return modelling_cohort(pd.read_parquet(clean_root / "ref_trial.parquet"))
