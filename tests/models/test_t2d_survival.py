"""CI-safe tests for the discrete-time survival model (Phase 3 Step c).

All data is fabricated in-memory — no parquet files required. Tests verify:
1. Forbidden features raise ValueError before any fit.
2. The survival panel contains no forbidden columns.
3. C-index returned by evaluate_survival is in [0, 1].
4. lead_time_curve has the required keys at every operating point.
"""

from __future__ import annotations

import numpy as np
import pandas as pd
import pytest

from models.t2d.survival import (
    FORBIDDEN,
    _assert_no_forbidden,
    _lead_time_curve,
    _make_pipeline,
    build_survival_panel,
    evaluate_survival,
    fit_survival_model,
    predict_cumulative_hazard,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def make_fake_participants(n: int = 200, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    n_visits_arr = rng.integers(10, 40, n)
    planned_dur = rng.integers(180, 720, n)
    dropped = rng.choice([True, False], n, p=[0.15, 0.85])
    dropout_vi = np.where(dropped, rng.integers(2, 38, n), -1)
    # time_to_event: droppers get a shorter time, completers get full duration
    tte = np.where(
        dropped,
        (dropout_vi.astype(float) / n_visits_arr) * planned_dur,
        planned_dur.astype(float),
    )
    event_observed = dropped.astype(int)

    return pd.DataFrame(
        {
            "participant_id": list(range(n)),
            "nct_id": np.repeat(["NCT001", "NCT002", "NCT003", "NCT004"], n // 4),
            "arm_type": rng.choice(["Active", "Placebo Comparator"], n),
            "phase": rng.choice(["PHASE2", "PHASE3"], n),
            "planned_duration_days": planned_dur,
            "n_visits": n_visits_arr,
            "n_sites": rng.integers(5, 50, n),
            "age_years": rng.normal(58, 10, n),
            "hba1c_pct": rng.normal(8, 1, n),
            "bmi": rng.normal(30, 5, n),
            "dropped": dropped,
            "censored": ~dropped,
            "event_observed": event_observed,
            "time_to_event": tte,
            "synthetic": True,
        }
    )


def make_fake_engagement(participants: pd.DataFrame, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for _, row in participants.iterrows():
        n_vis = int(row["n_visits"])
        pid = row["participant_id"]
        dropped = bool(row["dropped"])
        dropout_vi = int(rng.integers(2, max(3, n_vis - 1))) if dropped else n_vis
        # Truncate at dropout for droppers
        n_obs = dropout_vi if dropped else n_vis
        cum_miss = 0
        consec_miss = 0
        for vi in range(n_obs):
            missed = rng.choice([0, 1], p=[0.85, 0.15])
            if missed:
                cum_miss += 1
                consec_miss += 1
            else:
                consec_miss = 0
            rows.append(
                {
                    "participant_id": pid,
                    "visit_index": vi,
                    "attended": 1 - missed,
                    "missed": missed,
                    "cumulative_missed": cum_miss,
                    "consecutive_missed": consec_miss,
                    "miss_probability": rng.uniform(
                        0.05, 0.30
                    ),  # FORBIDDEN — not in features
                    "synthetic": True,
                }
            )
    return pd.DataFrame(rows)


@pytest.fixture(scope="module")
def fake_data() -> tuple[pd.DataFrame, pd.DataFrame]:
    participants = make_fake_participants(200, seed=42)
    engagement = make_fake_engagement(participants, seed=42)
    return participants, engagement


# ---------------------------------------------------------------------------
# Test 1 — Forbidden features raise ValueError
# ---------------------------------------------------------------------------


def test_forbidden_features_raise() -> None:
    """_assert_no_forbidden must raise if any forbidden column appears."""
    for bad_col in [
        "miss_probability",
        "dropped",
        "event_observed",
        "synthetic",
        "nct_id",
    ]:
        with pytest.raises(ValueError, match="Forbidden features"):
            _assert_no_forbidden([bad_col, "visit_index", "attended"])


def test_forbidden_features_clean_list_passes() -> None:
    """Clean feature list must not raise."""
    _assert_no_forbidden(["phase", "arm_type", "visit_index", "attended", "bmi"])


# ---------------------------------------------------------------------------
# Test 2 — Panel has no forbidden columns
# ---------------------------------------------------------------------------


def test_panel_no_forbidden_feature_columns(
    fake_data: tuple[pd.DataFrame, pd.DataFrame],
) -> None:
    """build_survival_panel must not include forbidden columns in the FEATURE set
    (CATEGORICAL_FEATURES + NUMERIC_FEATURES).  event_observed and time_to_event are
    allowed as label/metadata columns but must never be features fed to the model."""
    from models.t2d.survival import CATEGORICAL_FEATURES, NUMERIC_FEATURES

    participants, engagement = fake_data
    fold_ids = frozenset(participants["nct_id"].unique().tolist())
    panel = build_survival_panel(participants, engagement, fold_ids)

    feature_cols = set(CATEGORICAL_FEATURES + NUMERIC_FEATURES)
    forbidden_as_features = FORBIDDEN & feature_cols & set(panel.columns)
    assert not forbidden_as_features, (
        f"Forbidden columns used as features in panel: {forbidden_as_features}"
    )

    # Additionally: miss_probability must never appear in the panel at all
    assert "miss_probability" not in panel.columns, "miss_probability leaked into panel"


# ---------------------------------------------------------------------------
# Test 3 — C-index in [0, 1]
# ---------------------------------------------------------------------------


def test_c_index_in_range(fake_data: tuple[pd.DataFrame, pd.DataFrame]) -> None:
    """The C-index returned by evaluate_survival must be in [0, 1]."""
    participants, engagement = fake_data
    fold_ids = frozenset(participants["nct_id"].unique().tolist())

    panel = build_survival_panel(participants, engagement, fold_ids)
    pipeline = _make_pipeline()
    fit_survival_model(panel, pipeline)

    predicted_H = predict_cumulative_hazard(
        participants, engagement, pipeline, fold_ids
    )

    result = evaluate_survival(participants, predicted_H)
    c = result["c_index"]["overall"]
    assert 0.0 <= c <= 1.0, f"C-index out of range: {c}"


# ---------------------------------------------------------------------------
# Test 4 — lead_time_curve has required keys
# ---------------------------------------------------------------------------


def test_lead_time_curve_keys(fake_data: tuple[pd.DataFrame, pd.DataFrame]) -> None:
    """Every entry in lead_time_curve must contain t_flag, flagged_fraction,
    median_lead_time_visits, and threshold."""
    participants, engagement = fake_data
    fold_ids = frozenset(participants["nct_id"].unique().tolist())

    panel = build_survival_panel(participants, engagement, fold_ids)
    pipeline = _make_pipeline()
    fit_survival_model(panel, pipeline)

    curve = _lead_time_curve(engagement, participants, fold_ids, pipeline)
    assert len(curve) == 7, f"Expected 7 operating points, got {len(curve)}"
    required_keys = {
        "t_flag",
        "flagged_fraction",
        "median_lead_time_visits",
        "threshold",
    }
    for entry in curve:
        missing = required_keys - set(entry.keys())
        assert not missing, f"Missing keys {missing} in entry {entry}"


# ---------------------------------------------------------------------------
# Test 5 — panel y label: only droppers at their last visit have y=1
# ---------------------------------------------------------------------------


def test_panel_label_integrity(fake_data: tuple[pd.DataFrame, pd.DataFrame]) -> None:
    """In the panel, y=1 only at the last visit of a dropper; all others y=0."""
    participants, engagement = fake_data
    fold_ids = frozenset(participants["nct_id"].unique().tolist())
    panel = build_survival_panel(participants, engagement, fold_ids)

    # y=1 rows must be at last_visit_index
    pos_rows = panel[panel["y"] == 1]
    assert (pos_rows["visit_index"] == pos_rows["last_visit_index"]).all(), (
        "Some y=1 rows are not at the last visit"
    )

    # y=1 rows must correspond to event_observed=1
    assert (pos_rows["event_observed"] == 1).all(), (
        "y=1 rows contain non-dropper participants"
    )
