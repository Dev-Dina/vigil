"""Temporal + group-disjoint-by-``nct_id`` held-out split keyed on ``ref_trial.start_date``.

Earlier-starting trials -> train, later -> val -> test (specs/data.md "Evaluation contract":
held-out split is temporal, axis = ``ref_trial.start_date``). Whole trials (and whole calendar
dates) are assigned to a single fold, so one trial's participants never span splits AND the
folds are strictly ordered in time. Every invariant fails LOUD.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date

import pandas as pd

from ingestion.errors import LeakageError
from ingestion.leakage import assert_group_disjoint
from models.config import TEST_FRACTION, VAL_FRACTION


@dataclass(frozen=True)
class Split:
    """A temporal, group-disjoint partition of the modelling cohort by ``nct_id``."""

    train_ids: frozenset[str]
    val_ids: frozenset[str]
    test_ids: frozenset[str]
    train_dates: tuple[date, date]
    val_dates: tuple[date, date]
    test_dates: tuple[date, date]
    cutoffs: tuple[date, date]  # (train|val boundary, val|test boundary)

    @property
    def fold_sizes(self) -> dict[str, int]:
        return {
            "train": len(self.train_ids),
            "val": len(self.val_ids),
            "test": len(self.test_ids),
        }


def temporal_group_split(
    cohort: pd.DataFrame,
    *,
    nct_col: str = "nct_id",
    date_col: str = "start_date",
    val_frac: float = VAL_FRACTION,
    test_frac: float = TEST_FRACTION,
) -> Split:
    """Split the cohort temporally on ``start_date``, keeping whole dates in one fold.

    Cutoffs are chosen on the sorted distinct dates by cumulative trial count to approximate
    the requested fractions while never straddling a calendar date across folds. Fails loud
    (``LeakageError``) if any fold is empty or the temporal order is not strict.
    """
    if nct_col not in cohort.columns or date_col not in cohort.columns:
        raise ValueError(f"cohort must have '{nct_col}' and '{date_col}' columns")
    trials = cohort[[nct_col, date_col]].drop_duplicates(nct_col)
    if trials[date_col].isna().any():
        raise ValueError(f"{date_col} has nulls; the temporal axis must be non-null")
    n = len(trials)
    if n < 3:
        raise ValueError(f"need >= 3 trials for a 3-way temporal split, got {n}")

    # Per-distinct-date trial counts, oldest first.
    by_date = trials.groupby(date_col)[nct_col].count().sort_index()
    dates = list(by_date.index)
    cum = by_date.cumsum().to_numpy()

    train_target = round(n * (1.0 - val_frac - test_frac))
    val_target = round(n * (1.0 - test_frac))

    # Smallest date index whose cumulative count reaches each target -> whole-date cutoff.
    def _cut(target: int, lo: int) -> int:
        for i in range(lo, len(dates)):
            if cum[i] >= target:
                return i
        return len(dates) - 1

    # train_end leaves room for at least one date each for val and test.
    train_end = min(_cut(train_target, 0), len(dates) - 3)
    # val starts strictly after train_end and leaves at least one date for test.
    val_end = min(max(_cut(val_target, train_end + 1), train_end + 1), len(dates) - 2)
    if not (0 <= train_end < val_end < len(dates) - 1):
        raise LeakageError(
            f"could not form three non-empty temporal date-bands from {len(dates)} dates"
        )

    train_cut = dates[train_end]
    val_cut = dates[val_end]

    sd = trials.set_index(nct_col)[date_col]
    train_ids = frozenset(sd[sd <= train_cut].index)
    val_ids = frozenset(sd[(sd > train_cut) & (sd <= val_cut)].index)
    test_ids = frozenset(sd[sd > val_cut].index)

    split = Split(
        train_ids=train_ids,
        val_ids=val_ids,
        test_ids=test_ids,
        train_dates=_bounds(sd, train_ids),
        val_dates=_bounds(sd, val_ids),
        test_dates=_bounds(sd, test_ids),
        cutoffs=(train_cut, val_cut),
    )
    assert_split_valid(split, expected_ids=frozenset(trials[nct_col]))
    return split


def assign_folds(
    cohort: pd.DataFrame, split: Split, *, nct_col: str = "nct_id"
) -> pd.DataFrame:
    """Return ``cohort`` with a ``fold`` column in {train, val, test}."""
    fold = pd.Series("", index=cohort.index, dtype="object")
    fold[cohort[nct_col].isin(split.train_ids)] = "train"
    fold[cohort[nct_col].isin(split.val_ids)] = "val"
    fold[cohort[nct_col].isin(split.test_ids)] = "test"
    out = cohort.copy()
    out["fold"] = fold
    if (out["fold"] == "").any():
        orphan = out.loc[out["fold"] == "", nct_col].unique()[:5]
        raise LeakageError(
            f"rows not assigned to any fold (nct_id absent from split): {orphan}"
        )
    return out


def assert_split_valid(split: Split, *, expected_ids: frozenset[str]) -> None:
    """Fail loud unless the split is group-disjoint, complete, and strictly temporal."""
    assert_group_disjoint(
        {"train": split.train_ids, "val": split.val_ids, "test": split.test_ids}
    )
    union = split.train_ids | split.val_ids | split.test_ids
    if union != expected_ids:
        raise LeakageError("split folds do not partition the cohort exactly")
    if not (split.train_ids and split.val_ids and split.test_ids):
        raise LeakageError(f"a temporal fold is empty: {split.fold_sizes}")
    # Strict temporal order (whole-date assignment guarantees no date straddles a boundary).
    if not (
        split.train_dates[1] < split.val_dates[0]
        and split.val_dates[1] < split.test_dates[0]
    ):
        raise LeakageError(
            f"temporal order violated: train{split.train_dates} val{split.val_dates} "
            f"test{split.test_dates}"
        )


def _bounds(sd: pd.Series, ids: frozenset[str]) -> tuple[date, date]:
    sub = sd[sd.index.isin(ids)]
    return (sub.min(), sub.max())
