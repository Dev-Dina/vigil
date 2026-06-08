"""CI-safe, fast tests for the T2D sequence layer (Task 3) + the 1b structural feature set.

Everything is fabricated IN-MEMORY (a few trials, a few participants, short sequences) so the
tests never touch ``data/synthetic/t2d/`` (git-ignored). The sequence pipeline runs for 1-2
epochs on CPU and must: run + return the metric panel; exclude ``miss_probability`` / ``synthetic``
/ provenance / outcome columns from the feature set (asserted programmatically); fire the
leakage assertion on an injected violation; and be deterministic (same seed -> same first-batch
loss). A small 1b test asserts the structural-only feature set excludes engagement.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from ingestion.errors import LeakageError
from models.splits import temporal_group_split
from models.t2d.baseline_synthetic import train_synthetic_structural
from models.t2d.sequence import SequenceConfig, train_sequence_model
from models.t2d.synthetic_data import (
    FORBIDDEN_FEATURES,
    SEQ_NUMERIC,
    STATIC_CATEGORICAL,
    STATIC_NUMERIC,
    SyntheticT2D,
    assert_no_forbidden_features,
)


# --- in-memory fabrication ------------------------------------------------------------------
def _fabricate(seed: int = 0) -> tuple[SyntheticT2D, object]:
    """A tiny synthetic cohort: 6 trials (>=3 distinct start_dates), a few participants each."""
    rng = np.random.default_rng(seed)
    trials = []
    parts = []
    eng_rows = []
    pid = 0
    # 6 trials across 6 distinct dates so the temporal 3-way split is well-formed.
    for ti in range(6):
        nct = f"NCT{ti:08d}"
        trials.append({"nct_id": nct, "phase": "PHASE3" if ti % 2 else "PHASE2",
                       "start_date": date(2000 + ti, 1, 1)})
        n_visits = 8
        for _ in range(8):  # 8 participants/trial
            dropped = bool(rng.random() < 0.4)
            if dropped:
                dvi = int(rng.integers(3, n_visits))  # drops mid-sequence
                obs = dvi + 1
            else:
                dvi = -1
                obs = n_visits
            parts.append({
                "participant_id": pid,
                "nct_id": nct,
                "arm_id": f"{nct}A",
                "arm_type": "Experimental" if rng.random() < 0.5 else "Placebo Comparator",
                "phase": trials[-1]["phase"],
                "n_sites": int(rng.integers(1, 20)),
                "planned_duration_days": int(rng.integers(180, 720)),
                "n_visits": n_visits,
                "age_years": float(rng.normal(60, 8)),
                "age_baseline_imputed": False,
                "sex": "MALE" if rng.random() < 0.5 else "FEMALE",
                "hba1c_pct": float(rng.normal(7.5, 1.0)),
                "hba1c_baseline_imputed": True,
                "bmi": float(rng.normal(30, 4)),
                "bmi_baseline_imputed": True,
                "arm_real_dropout_rate": 0.3,
                "synthetic": True,
                "dropped": dropped,
                "censored": not dropped,
                "dropout_visit_index": float(dvi) if dropped else np.nan,
                "dropout_reason": "WITHDRAWAL_BY_SUBJECT" if dropped else "",
            })
            cum = 0
            cons = 0
            for vi in range(obs):
                missed = bool(rng.random() < (0.6 if dropped and vi >= 2 else 0.2))
                cum += int(missed)
                cons = cons + 1 if missed else 0
                eng_rows.append({
                    "participant_id": pid,
                    "visit_index": vi,
                    "attended": not missed,
                    "missed": missed,
                    "cumulative_missed": cum,
                    "consecutive_missed": cons,
                    "miss_probability": float(rng.random()),
                    "synthetic": True,
                })
            pid += 1
    participants = pd.DataFrame(parts)
    engagement = pd.DataFrame(eng_rows)
    trials_df = pd.DataFrame(trials)
    split = temporal_group_split(trials_df)
    return SyntheticT2D(participants=participants, engagement=engagement), split


@pytest.fixture(scope="module")
def fabricated():
    return _fabricate()


# --- sequence pipeline runs + returns a panel ----------------------------------------------
def test_sequence_runs_and_returns_panel(fabricated) -> None:
    synth, split = fabricated
    cfg = SequenceConfig(epochs=2, hidden_size=8, batch_size=32, max_train_participants=1000)
    res = train_sequence_model(synth, split, cfg=cfg)
    panel = res["panel"]
    assert {"pr_auc", "recall_at_p50", "brier"} <= set(panel["test"]["overall"])
    assert "median_lead_time_visits" in panel["lead_time"]
    assert set(panel["test"]["per_stratum"]) == {"arm_type", "phase"}


# --- feature-set governance (programmatic) --------------------------------------------------
def test_forbidden_columns_not_in_feature_set() -> None:
    feature_cols = set(SEQ_NUMERIC) | set(STATIC_NUMERIC) | set(STATIC_CATEGORICAL)
    assert "miss_probability" not in feature_cols
    assert "synthetic" not in feature_cols
    for prov in ("age_baseline_imputed", "hba1c_baseline_imputed", "bmi_baseline_imputed"):
        assert prov not in feature_cols
    for outcome in ("dropped", "censored", "arm_real_dropout_rate", "dropout_visit_index"):
        assert outcome not in feature_cols
    # every feature column is NOT in the forbidden set
    assert feature_cols.isdisjoint(FORBIDDEN_FEATURES)


def test_leakage_assertion_fires_on_injected_violation() -> None:
    # a feature set that smuggles in the latent hazard MUST raise.
    with pytest.raises(LeakageError):
        assert_no_forbidden_features([*SEQ_NUMERIC, "miss_probability"])
    with pytest.raises(LeakageError):
        assert_no_forbidden_features(["synthetic"])
    with pytest.raises(LeakageError):
        assert_no_forbidden_features(["dropped"])


# --- determinism ----------------------------------------------------------------------------
def test_determinism_same_seed_same_first_batch_loss(fabricated) -> None:
    synth, split = fabricated
    cfg = SequenceConfig(epochs=1, hidden_size=8, batch_size=32, max_train_participants=1000)
    a = train_sequence_model(synth, split, cfg=cfg)["panel"]["first_batch_loss"]
    b = train_sequence_model(synth, split, cfg=cfg)["panel"]["first_batch_loss"]
    assert a == pytest.approx(b)


# --- 1b structural-only feature set excludes engagement -------------------------------------
def test_structural_baseline_excludes_engagement(fabricated, tmp_path) -> None:
    synth, split = fabricated
    metrics = train_synthetic_structural(synth, split, out_root=tmp_path)
    feat = metrics["feature_names"]
    # no per-visit / trajectory feature leaks into the structural-only matrix
    for traj in SEQ_NUMERIC:
        assert all(not name.startswith(traj) for name in feat), f"{traj} leaked into 1b"
    assert "miss_probability" not in feat
    # the structural panel is present and uses arm_type/phase strata
    assert set(metrics["test"]["per_stratum_gbt"]) == {"arm_type", "phase"}
    assert {"pr_auc", "recall_at_p50", "brier"} <= set(metrics["test"]["gbt"])
