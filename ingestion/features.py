"""Feature computation with decision-time-t / horizon-H labelling and leakage rules.

At a decision time ``t`` during a participant's active enrollment we predict a dropout
event in ``[t, t+H)`` (default H=28d). Every feature is computed from observations strictly
before ``t``. Time-varying features are emitted as current / 7d / 28d / slope so the model
can learn deterioration (the trend, not the level).

The leakage rules in specs/data.md are non-negotiable and enforced by
:func:`assert_no_leakage`, which fails loud.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd

from ingestion.config import DEFAULT_HORIZON_DAYS
from ingestion.errors import LeakageError

# Static features carried through from the participant table (no outcome-derived columns).
STATIC_FEATURES = [
    "age_years",
    "baseline_severity",
    "socioeconomic_proxy",
    "travel_friction",
    "prior_trial_experience",
    "comorbidity_count",
    "phase",
    "therapeutic_area",
    "arm_type",
    "planned_duration_days",
    "n_sites",
]

# Columns that encode the outcome and must NEVER become features.
_OUTCOME_COLUMNS = {
    "dropped",
    "time_to_event_days",
    "censored",
    "dropout_reason",
    "dropout_rate",
}

# ``synthetic`` is metadata, never a feature.
_FORBIDDEN_FEATURE_COLUMNS = _OUTCOME_COLUMNS | {"synthetic"}


@dataclass
class FeatureMatrix:
    X: pd.DataFrame  # one row per (participant, decision_time t)
    feature_columns: list[str]
    horizon_days: int


def _trailing_stats(
    series: pd.DataFrame, value_col: str, t: int
) -> tuple[float, float, float, float]:
    """Return (current, 7d-mean, 28d-mean, slope) over observations with day < t."""
    past = series[series["day"] < t]
    if past.empty:
        return 0.0, 0.0, 0.0, 0.0
    current = float(past.iloc[-1][value_col])
    w7 = past[past["day"] >= t - 7][value_col]
    w28 = past[past["day"] >= t - 28][value_col]
    mean7 = float(w7.mean()) if len(w7) else current
    mean28 = float(w28.mean()) if len(w28) else current
    # Slope: 7d-vs-28d ratio captures deterioration (declining ratio = precursor).
    slope = mean7 - mean28
    return current, mean7, mean28, slope


def build_features(
    participants: pd.DataFrame,
    engagement: pd.DataFrame,
    *,
    horizon_days: int = DEFAULT_HORIZON_DAYS,
    decision_offset_days: int = 14,
    cutoff_day: int | None = None,
) -> FeatureMatrix:
    """Compute one sample per participant at a single decision time ``t``.

    ``t = decision_offset_days``. Label = dropout event in ``[t, t+H)``. Participants who
    are right-censored before ``t+H`` and have not dropped are excluded from labels they
    cannot have (never silently labelled "no dropout").
    """
    t = decision_offset_days
    rows: list[dict] = []
    eng_by_p = {pid: g for pid, g in engagement.groupby("participant_id")}

    for prow in participants.itertuples():
        series = eng_by_p.get(prow.participant_id)
        if series is None or series.empty:
            continue
        last_obs_day = int(series["day"].max())
        # Participant must be active at t (have observations before t).
        if last_obs_day < t:
            # Already silent before the decision time -> excluded from this t.
            continue

        # Censoring: if not dropped and observation window ends before t+H, the label is
        # unknown -> exclude.
        event_in_horizon = bool(
            prow.dropped and (t <= prow.time_to_event_days < t + horizon_days)
        )
        if not prow.dropped:
            # Right-censored at last_obs_day. Only keep if we observe the full horizon.
            horizon_observed = cutoff_day if cutoff_day is not None else last_obs_day
            if horizon_observed < t + horizon_days:
                continue
        label = int(event_in_horizon)

        feat: dict = {
            "participant_id": prow.participant_id,
            "nct_id": prow.nct_id,
            "t": t,
            "label": label,
            "max_feature_day": int(series[series["day"] < t]["day"].max())
            if (series["day"] < t).any()
            else -1,
        }
        for col in STATIC_FEATURES:
            feat[f"static__{col}"] = getattr(prow, col)

        for value_col, prefix in [
            ("diary_completion", "diary"),
            ("app_opens", "app_opens"),
            ("reminder_response_latency_h", "latency"),
            ("symptom_log_count", "symptom"),
        ]:
            cur, m7, m28, slope = _trailing_stats(series, value_col, t)
            feat[f"tv__{prefix}__current"] = cur
            feat[f"tv__{prefix}__7d"] = m7
            feat[f"tv__{prefix}__28d"] = m28
            feat[f"tv__{prefix}__slope"] = slope

        past = series[series["day"] < t]
        feat["tv__days_since_last_entry"] = (
            int(t - past["day"].max()) if not past.empty else t
        )
        rows.append(feat)

    X = pd.DataFrame(rows)
    feature_cols = [c for c in X.columns if c.startswith(("static__", "tv__"))]
    return FeatureMatrix(X=X, feature_columns=feature_cols, horizon_days=horizon_days)


def group_split_by_trial(
    X: pd.DataFrame,
    *,
    seed: int,
    val_frac: float = 0.15,
    test_frac: float = 0.15,
) -> dict[str, pd.DataFrame]:
    """Split on ``nct_id`` so one trial's participants never span splits."""
    rng = np.random.default_rng(seed)
    trials = np.array(sorted(X["nct_id"].unique()))
    rng.shuffle(trials)
    n = len(trials)
    n_test = max(1, int(round(n * test_frac)))
    n_val = max(1, int(round(n * val_frac)))
    test_t = set(trials[:n_test])
    val_t = set(trials[n_test : n_test + n_val])
    train_t = set(trials[n_test + n_val :])
    return {
        "train": X[X["nct_id"].isin(train_t)].copy(),
        "val": X[X["nct_id"].isin(val_t)].copy(),
        "test": X[X["nct_id"].isin(test_t)].copy(),
    }


def fit_scaler_on_train(
    splits: dict[str, pd.DataFrame], feature_columns: list[str]
) -> dict[str, dict[str, float]]:
    """Fit standardisation stats on TRAIN ONLY. Returns mean/std per numeric feature."""
    train = splits["train"]
    stats: dict[str, dict[str, float]] = {}
    for col in feature_columns:
        if pd.api.types.is_numeric_dtype(train[col]):
            mean = float(train[col].mean())
            std = float(train[col].std() or 1.0)
            stats[col] = {"mean": mean, "std": std}
    return stats


def assert_no_leakage(
    fm: FeatureMatrix,
    splits: dict[str, pd.DataFrame],
) -> None:
    """Fail loud on any leakage-rule violation (specs/data.md)."""
    X = fm.X

    # 1. No future data: max feature timestamp < t for every sample.
    bad = X[X["max_feature_day"] >= X["t"]]
    if len(bad):
        raise LeakageError(
            f"{len(bad)} samples have a feature observation at day >= t "
            f"(future leakage)."
        )

    # 2. No outcome-derived feature columns.
    leaked = [c for c in fm.feature_columns if _is_outcome_derived(c)]
    if leaked:
        raise LeakageError(f"Outcome-derived feature columns present: {leaked}")
    if any(c in fm.feature_columns for c in _FORBIDDEN_FEATURE_COLUMNS):
        raise LeakageError(
            "A forbidden (outcome/metadata) column is used as a feature."
        )

    # 3. Group split: no nct_id appears in two splits.
    seen: dict[str, str] = {}
    for name, df in splits.items():
        for nct in df["nct_id"].unique():
            if nct in seen and seen[nct] != name:
                raise LeakageError(
                    f"nct_id {nct} appears in both {seen[nct]} and {name} splits."
                )
            seen[nct] = name

    # 4. Censoring: every retained sample has a known label (0 or 1).
    if not X["label"].isin((0, 1)).all():
        raise LeakageError("Found samples with an unknown/non-binary label.")


def _is_outcome_derived(col: str) -> bool:
    name = col.split("__")[-1]
    return name in _OUTCOME_COLUMNS or "dropout" in name or "time_to_event" in name
