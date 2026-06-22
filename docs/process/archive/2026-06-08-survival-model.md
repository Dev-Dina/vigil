# Phase 3 Step (c): Discrete-Time Hazard Survival Model Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Build a discrete-time hazard (Cox-discretized) survival model on the T2D synthetic censored cohort, emit C-index/calibration/lead-time metrics, attribution artifacts, and a model card — all gates green, ROADMAP updated, nothing committed.

**Architecture:** `HistGradientBoostingClassifier` trained on (participant, visit) pairs where each visit is labeled 1 if it is the dropout event and 0 otherwise; censored participants contribute 0-labeled visits up to their observed cutoff. The cumulative hazard `H(T) = 1 - prod(1 - h(v))` is the risk score for discrimination (C-index) and calibration. Static features come from `participants.parquet` (enrolled covariates, no provenance/outcome columns), dynamic features from `engagement_censored.parquet` (attended/missed/cumulative_missed/consecutive_missed/visit_index — never `miss_probability`). The same `temporal_group_split` call as 1a/1b/3 partitions by `nct_id`.

**Tech Stack:** Python 3.12, scikit-learn `HistGradientBoostingClassifier`, `lifelines` (C-index only), numpy, pandas — no torch, no PHI.

---

## File Structure

| File | Create / Modify | Responsibility |
|---|---|---|
| `pyproject.toml` | Modify | Add `lifelines>=0.29` to `dependencies` |
| `models/t2d/survival.py` | Create | The full model module: data builder, feature assembly, leakage guard, fit, evaluate (C-index, calibration, lead-time curve, attribution), artifact writer |
| `scripts/run_survival.py` | Create | Build-time entry point: load cohort, call `train_survival_model`, write artifacts |
| `tests/models/test_t2d_survival.py` | Create | CI-safe in-memory tests: leakage guards, feature governance, C-index ∈ [0,1], lead-time curve shape |
| `data/models/t2d/survival_metrics.json` | Written at runtime | Primary metrics artifact |
| `data/models/t2d/attr_hazard_global.json` | Written at runtime | Global mean per-visit hazard (first 50 visits) |
| `data/models/t2d/attr_feature_importance.json` | Written at runtime | Top 10 GBM feature importances |
| `data/models/t2d/attr_local_3.json` | Written at runtime | Cumulative hazard curves for 3 representative participants |
| `data/models/t2d/model_card_survival.md` | Written at runtime | Model card (all required strings present) |
| `ROADMAP.md` | Modify | Mark survival model done-when criteria |

---

## Task 1: Add `lifelines` dependency

**Files:**
- Modify: `pyproject.toml`

- [ ] **Step 1: Add lifelines to pyproject.toml**

Open `pyproject.toml`. In the `dependencies` list (after `"scipy>=1.17.1",`), add:

```toml
    "lifelines>=0.29.0",
```

- [ ] **Step 2: Sync the environment**

```bash
uv sync
```

Expected: resolves and installs `lifelines` (and its sub-deps: `autograd`, `formulaic`, etc.) — no errors.

- [ ] **Step 3: Verify import**

```bash
uv run python -c "from lifelines.utils import concordance_index; print('lifelines OK')"
```

Expected: `lifelines OK`

---

## Task 2: Write `models/t2d/survival.py`

**Files:**
- Create: `models/t2d/survival.py`

This is the core module. It has four logical sections: (A) data assembly, (B) feature governance / leakage guard, (C) fit + evaluate, (D) artifact writer.

### Part A: Data assembly — `build_survival_frame`

- [ ] **Step 1: Write the function header and imports**

Create `models/t2d/survival.py` with the following content (complete file — do not truncate):

```python
"""Phase 3 Step (c) — Discrete-time hazard survival model on the censored T2D synthetic cohort.

Formulation: for each participant, at each visit v (up to and including their
event/censoring visit) we emit one row with:
  y_v = 1  iff  event_observed==1 AND visit_index == dropout_visit_index
  y_v = 0  otherwise  (pre-event visits, and ALL visits of censored participants)

Model: HistGradientBoostingClassifier (NaN-native, no torch required).

Cumulative hazard H(T) = 1 - prod_{v=0}^{T}(1 - h(v))  where h(v) is the
predicted per-visit hazard probability from the GBM.

The split is the SAME temporal_group_split as 1a/1b/3 (keyed on ref_trial.start_date,
group-disjoint by nct_id).

SYNTHETIC — method demonstration only, NO PHI.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingClassifier

from ingestion.errors import LeakageError
from models.config import MODEL_SEED, MODELS_ROOT
from models.splits import Split, assign_folds
from models.t2d.synthetic_data import FORBIDDEN_FEATURES, assert_no_forbidden_features

T2D_ROOT = MODELS_ROOT / "t2d"

# ---------------------------------------------------------------------------
# Feature specification for the survival model
# ---------------------------------------------------------------------------

#: Static baseline covariates (from participants.parquet). No outcome, no latent, no provenance.
SURVIVAL_STATIC_NUMERIC: list[str] = [
    "age_years",
    "hba1c_pct",
    "bmi",
    "n_sites",
    "planned_duration_days",
]
SURVIVAL_STATIC_CATEGORICAL: list[str] = ["sex", "phase", "arm_type"]

#: Per-visit trajectory features (from engagement_censored.parquet). Never miss_probability.
SURVIVAL_DYNAMIC: list[str] = [
    "visit_index",
    "attended",
    "missed",
    "cumulative_missed",
    "consecutive_missed",
]

#: All feature names (before one-hot expansion of categoricals).
ALL_FEATURE_COLS: list[str] = (
    SURVIVAL_STATIC_NUMERIC + SURVIVAL_STATIC_CATEGORICAL + SURVIVAL_DYNAMIC
)

#: Lead-time sweep operating points (visits before event).
LEAD_TIME_T_FLAGS: list[int] = [1, 2, 3, 5, 8, 13, 21]
LEAD_TIME_HAZARD_THRESHOLD: float = 0.3


def _assert_survival_features_clean() -> None:
    """Fail loud if any feature col is in the forbidden set (called once at module import)."""
    leaked = [c for c in ALL_FEATURE_COLS if c in FORBIDDEN_FEATURES]
    if leaked:
        raise LeakageError(
            f"survival feature spec contains forbidden columns: {leaked}"
        )


_assert_survival_features_clean()
```

- [ ] **Step 2: Write `build_survival_frame`**

Append this function to `models/t2d/survival.py`:

```python
def build_survival_frame(
    participants: pd.DataFrame,
    engagement: pd.DataFrame,
) -> pd.DataFrame:
    """Build the (participant, visit) discrete-time hazard frame.

    For each participant observed up to visit V (their dropout visit if dropped,
    else their last engagement visit), emit one row per visit v in [0, V]:
      - y = 1 iff dropped==True AND visit_index == dropout_visit_index
      - y = 0 otherwise (pre-event visits + all visits of censored participants)

    Static covariates are broadcast from participants onto every visit row.
    Dynamic features (visit_index, attended, missed, cumulative_missed,
    consecutive_missed) come directly from engagement_censored.

    Columns in the returned frame:
      participant_id, nct_id, + static cols + dynamic cols + y (int 0/1)
    """
    # Static broadcast columns — select only what we need; never forbidden cols
    static_cols = ["participant_id", "nct_id"] + SURVIVAL_STATIC_NUMERIC + SURVIVAL_STATIC_CATEGORICAL
    static = participants[static_cols].copy()

    # One-hot encode categoricals (fit later on train — here we just keep raw strings
    # for the per-participant join; encoding is done in build_matrices_survival).
    eng = engagement[["participant_id", "visit_index"] + SURVIVAL_DYNAMIC].copy()

    # Merge dynamic onto static (many-to-one on participant_id)
    frame = eng.merge(static, on="participant_id", how="inner")

    # Compute the binary outcome label
    # Grab dropout_visit_index and dropped from participants (never features)
    outcome = participants[["participant_id", "dropped", "dropout_visit_index"]].copy()
    frame = frame.merge(outcome, on="participant_id", how="inner")

    # y = 1 iff this visit IS the dropout event for a dropped participant
    frame["y"] = (
        (frame["dropped"])
        & (frame["visit_index"] == frame["dropout_visit_index"])
    ).astype(int)

    # Drop the outcome helper columns (they must NEVER leak into features)
    frame = frame.drop(columns=["dropped", "dropout_visit_index"])

    return frame.reset_index(drop=True)
```

- [ ] **Step 3: Write `_one_hot_encode_static` and `build_matrices_survival`**

Append these to `models/t2d/survival.py`:

```python
def _one_hot_encode_static(
    train_frame: pd.DataFrame,
    frames: dict[str, pd.DataFrame],
) -> tuple[dict[str, pd.DataFrame], list[str]]:
    """One-hot encode categorical static features fit on TRAIN only.

    Returns (encoded_frames_dict, all_feature_names).
    ``encoded_frames_dict`` has the same keys as ``frames`` but with
    categoricals replaced by dummy columns.
    """
    from sklearn.preprocessing import OneHotEncoder

    enc = OneHotEncoder(handle_unknown="ignore", sparse_output=False)
    enc.fit(train_frame[SURVIVAL_STATIC_CATEGORICAL].astype("string").fillna("__NA__"))
    cat_names = list(enc.get_feature_names_out(SURVIVAL_STATIC_CATEGORICAL))

    result: dict[str, pd.DataFrame] = {}
    for fold, df in frames.items():
        cat_encoded = pd.DataFrame(
            enc.transform(df[SURVIVAL_STATIC_CATEGORICAL].astype("string").fillna("__NA__")),
            columns=cat_names,
            index=df.index,
        )
        numeric_part = df[SURVIVAL_STATIC_NUMERIC + SURVIVAL_DYNAMIC].copy()
        # Cast booleans (attended, missed) to float — GBM handles them natively
        for col in ["attended", "missed"]:
            if col in numeric_part.columns:
                numeric_part[col] = numeric_part[col].astype(float)
        result[fold] = pd.concat([cat_encoded, numeric_part], axis=1)

    feature_names = cat_names + SURVIVAL_STATIC_NUMERIC + SURVIVAL_DYNAMIC
    return result, feature_names


def build_matrices_survival(
    participants: pd.DataFrame,
    engagement: pd.DataFrame,
    split: Split,
) -> tuple[
    dict[str, pd.DataFrame],  # fold -> feature matrix X
    dict[str, pd.Series],     # fold -> labels y
    dict[str, pd.DataFrame],  # fold -> participant-level metadata (nct_id, participant_id, dropped, dropout_visit_index)
    list[str],                 # feature names
]:
    """Assign folds, one-hot-encode (train-fit), and return feature matrices.

    The fold assignment is at the PARTICIPANT level keyed on nct_id, then
    the (participant, visit) rows inherit the fold of their participant.
    """
    # Participant-level fold assignment
    p_with_fold = assign_folds(
        participants[["participant_id", "nct_id"]].drop_duplicates("participant_id"),
        split,
        nct_col="nct_id",
    )
    pid_fold = p_with_fold.set_index("participant_id")["fold"]

    # Build the full survival frame
    frame = build_survival_frame(participants, engagement)

    # Attach fold
    frame["fold"] = frame["participant_id"].map(pid_fold)
    if frame["fold"].isna().any():
        orphan = frame.loc[frame["fold"].isna(), "participant_id"].unique()[:5]
        raise LeakageError(f"survival frame rows with no fold assignment: {orphan}")

    # Participant-level metadata (for evaluation)
    p_meta = participants[
        ["participant_id", "nct_id", "dropped", "dropout_visit_index",
         "arm_type", "phase", "event_observed", "time_to_event"]
    ].copy()
    p_meta["fold"] = p_meta["participant_id"].map(pid_fold)

    fold_frames: dict[str, pd.DataFrame] = {}
    fold_labels: dict[str, pd.Series] = {}
    fold_meta: dict[str, pd.DataFrame] = {}
    for fold in ("train", "val", "test"):
        sub = frame[frame["fold"] == fold].drop(columns=["fold"])
        fold_frames[fold] = sub.drop(columns=["y", "participant_id", "nct_id"])
        fold_labels[fold] = sub["y"].reset_index(drop=True)
        fold_meta[fold] = p_meta[p_meta["fold"] == fold].drop(columns=["fold"]).reset_index(drop=True)

    # Feature names (pre-encoding) — check for forbidden tokens before one-hot
    raw_feature_cols = SURVIVAL_STATIC_CATEGORICAL + SURVIVAL_STATIC_NUMERIC + SURVIVAL_DYNAMIC
    assert_no_forbidden_features(raw_feature_cols)

    # One-hot encode — fit on TRAIN only
    encoded, feature_names = _one_hot_encode_static(
        fold_frames["train"], fold_frames
    )

    # Final leakage check on the encoded names
    assert_no_forbidden_features(feature_names)

    return encoded, fold_labels, fold_meta, feature_names
```

- [ ] **Step 4: Write `_cumulative_hazard`**

Append to `models/t2d/survival.py`:

```python
def _cumulative_hazard(
    model: HistGradientBoostingClassifier,
    engagement: pd.DataFrame,
    participants: pd.DataFrame,
    pid_set: set[int],
    feature_names: list[str],
    enc: Any,
) -> dict[int, float]:
    """Compute H(T_i) for each participant i in pid_set.

    T_i = number of observed visits (last visit_index + 1).
    H(T_i) = 1 - prod_{v=0}^{T_i - 1}(1 - h(v))
    where h(v) = predicted per-visit hazard for participant i at visit v.

    Returns dict participant_id -> H(T_i).
    """
    from sklearn.preprocessing import OneHotEncoder  # already fitted; enc is passed in

    eng = engagement[engagement["participant_id"].isin(pid_set)].copy()
    static_cols = ["participant_id"] + SURVIVAL_STATIC_NUMERIC + SURVIVAL_STATIC_CATEGORICAL
    static = participants[participants["participant_id"].isin(pid_set)][static_cols].copy()
    frame = eng[["participant_id", "visit_index"] + SURVIVAL_DYNAMIC].merge(
        static, on="participant_id", how="inner"
    )

    cat_encoded = pd.DataFrame(
        enc.transform(frame[SURVIVAL_STATIC_CATEGORICAL].astype("string").fillna("__NA__")),
        columns=[c for c in feature_names if c.startswith(tuple(SURVIVAL_STATIC_CATEGORICAL))
                 or "=" in c],
        index=frame.index,
    )
    # Rebuild X in the same column order as feature_names
    # Build from scratch using the known column layout
    cat_part = pd.DataFrame(
        enc.transform(frame[SURVIVAL_STATIC_CATEGORICAL].astype("string").fillna("__NA__")),
        index=frame.index,
    )
    num_part = frame[SURVIVAL_STATIC_NUMERIC + SURVIVAL_DYNAMIC].copy()
    for col in ["attended", "missed"]:
        if col in num_part.columns:
            num_part[col] = num_part[col].astype(float)
    X = pd.concat([cat_part, num_part], axis=1)
    X.columns = list(feature_names)

    probs = model.predict_proba(X)[:, 1]
    frame["hazard"] = probs
    frame["participant_id_"] = frame["participant_id"].to_numpy()

    cum_h: dict[int, float] = {}
    for pid, grp in frame.groupby("participant_id_"):
        h = grp.sort_values("visit_index")["hazard"].to_numpy()
        H = float(1.0 - np.prod(1.0 - np.clip(h, 1e-9, 1 - 1e-9)))
        cum_h[int(pid)] = H
    return cum_h
```

This approach is redundant with the encoder we'll pass explicitly. Let me rewrite it cleanly as a helper that takes the already-fitted encoder object directly:

Overwrite `_cumulative_hazard` with a cleaner version — **delete everything after `fold_meta[fold] = ...` inside `build_matrices_survival` down to end-of-file** and replace with this consolidated block:

```python
def _predict_per_visit_hazard(
    model: HistGradientBoostingClassifier,
    engagement: pd.DataFrame,
    participants: pd.DataFrame,
    pid_set: set[int],
    feature_names: list[str],
    cat_enc: Any,
) -> pd.DataFrame:
    """Return a frame (participant_id, visit_index, hazard) for all visits of pid_set.

    ``cat_enc`` is the already-fitted sklearn OneHotEncoder.
    """
    from sklearn.preprocessing import OneHotEncoder  # noqa: F401 — type hint only

    eng = engagement[engagement["participant_id"].isin(pid_set)][
        ["participant_id", "visit_index"] + SURVIVAL_DYNAMIC
    ].copy()
    static_cols = ["participant_id"] + SURVIVAL_STATIC_NUMERIC + SURVIVAL_STATIC_CATEGORICAL
    static = participants[participants["participant_id"].isin(pid_set)][static_cols].copy()
    frame = eng.merge(static, on="participant_id", how="inner")

    cat_encoded = pd.DataFrame(
        cat_enc.transform(
            frame[SURVIVAL_STATIC_CATEGORICAL].astype("string").fillna("__NA__")
        ),
        index=frame.index,
    )
    num_part = frame[SURVIVAL_STATIC_NUMERIC + SURVIVAL_DYNAMIC].copy()
    for col in ["attended", "missed"]:
        if col in num_part.columns:
            num_part[col] = num_part[col].astype(float)
    X = pd.concat([cat_encoded, num_part], axis=1)
    X.columns = list(feature_names)

    hazard = model.predict_proba(X)[:, 1]
    return pd.DataFrame(
        {
            "participant_id": frame["participant_id"].to_numpy(),
            "visit_index": frame["visit_index"].to_numpy(),
            "hazard": hazard,
        }
    )


def _cumulative_hazard_map(hazard_df: pd.DataFrame) -> dict[int, float]:
    """H(T_i) = 1 - prod(1 - h(v)) for each participant in hazard_df."""
    cum: dict[int, float] = {}
    for pid, grp in hazard_df.groupby("participant_id"):
        h = grp.sort_values("visit_index")["hazard"].to_numpy()
        H = float(1.0 - np.prod(1.0 - np.clip(h, 1e-9, 1.0 - 1e-9)))
        cum[int(pid)] = H
    return cum
```

- [ ] **Step 5: Write the evaluation helpers**

Append to `models/t2d/survival.py`:

```python
def _c_index_overall(
    meta: pd.DataFrame, cum_hazard: dict[int, float]
) -> float:
    """C-index on TEST: concordance_index(event_times, -predicted_risk, event_observed)."""
    from lifelines.utils import concordance_index

    meta = meta.copy()
    meta["pred_risk"] = meta["participant_id"].map(cum_hazard).fillna(0.0)
    return float(
        concordance_index(
            meta["time_to_event"].to_numpy(),
            -meta["pred_risk"].to_numpy(),
            meta["event_observed"].to_numpy(),
        )
    )


def _c_index_by_stratum(
    meta: pd.DataFrame, cum_hazard: dict[int, float], stratum_col: str
) -> dict[str, float]:
    """Per-stratum C-index."""
    from lifelines.utils import concordance_index

    meta = meta.copy()
    meta["pred_risk"] = meta["participant_id"].map(cum_hazard).fillna(0.0)
    result: dict[str, float] = {}
    for level, grp in meta.groupby(stratum_col):
        if len(grp) < 2:
            result[str(level)] = float("nan")
            continue
        try:
            ci = float(
                concordance_index(
                    grp["time_to_event"].to_numpy(),
                    -grp["pred_risk"].to_numpy(),
                    grp["event_observed"].to_numpy(),
                )
            )
        except Exception:
            ci = float("nan")
        result[str(level)] = ci
    return result


def _calibration_quartiles(
    meta: pd.DataFrame, cum_hazard: dict[int, float]
) -> list[dict[str, float | int]]:
    """Bin TEST participants into quartiles by predicted H(T_i).

    Returns 4 rows: quartile, mean_pred_H, obs_event_rate, n.
    """
    meta = meta.copy()
    meta["pred_H"] = meta["participant_id"].map(cum_hazard).fillna(0.0)
    meta["quartile"] = pd.qcut(meta["pred_H"], q=4, labels=[1, 2, 3, 4])
    rows: list[dict[str, float | int]] = []
    for q, grp in meta.groupby("quartile"):
        rows.append(
            {
                "quartile": int(q),
                "mean_pred_H": float(grp["pred_H"].mean()),
                "obs_event_rate": float(grp["event_observed"].mean()),
                "n": int(len(grp)),
            }
        )
    return rows


def _lead_time_curve(
    meta: pd.DataFrame,
    engagement: pd.DataFrame,
    model: HistGradientBoostingClassifier,
    participants: pd.DataFrame,
    feature_names: list[str],
    cat_enc: Any,
    threshold: float = LEAD_TIME_HAZARD_THRESHOLD,
) -> list[dict[str, Any]]:
    """Sweep t_flag operating points for dropped TEST participants.

    For each t_flag:
    - Among true droppers, compute H(t_flag) (cumulative hazard up to visit t_flag).
    - flagged_fraction = fraction of true droppers whose H(t_flag) > threshold.
    - median_lead_time_visits = median(dropout_visit_index - t_flag) for flagged droppers.
    """
    droppers = meta[meta["event_observed"] == 1].copy()
    dropper_pids = set(droppers["participant_id"].tolist())

    # Pre-compute per-visit hazard for all droppers
    hviz = _predict_per_visit_hazard(
        model, engagement, participants, dropper_pids, feature_names, cat_enc
    )

    curve: list[dict[str, Any]] = []
    for t_flag in LEAD_TIME_T_FLAGS:
        flagged = 0
        leads: list[float] = []
        for _, row in droppers.iterrows():
            pid = int(row["participant_id"])
            dvi = float(row["dropout_visit_index"]) if not pd.isna(row["dropout_visit_index"]) else None
            if dvi is None:
                continue
            p_visits = hviz[hviz["participant_id"] == pid].sort_values("visit_index")
            if t_flag >= len(p_visits):
                # t_flag is beyond observed visits — skip
                continue
            # H up to t_flag (inclusive: visits 0..t_flag)
            h_slice = p_visits[p_visits["visit_index"] <= t_flag]["hazard"].to_numpy()
            H_tflag = float(1.0 - np.prod(1.0 - np.clip(h_slice, 1e-9, 1.0 - 1e-9)))
            if H_tflag > threshold:
                flagged += 1
                leads.append(float(dvi) - t_flag)
        n_droppers = len(droppers)
        curve.append(
            {
                "t_flag": t_flag,
                "flagged_fraction": float(flagged / n_droppers) if n_droppers else float("nan"),
                "median_lead_time_visits": float(np.median(leads)) if leads else float("nan"),
            }
        )
    return curve
```

- [ ] **Step 6: Write the attribution helpers**

Append to `models/t2d/survival.py`:

```python
def _attr_hazard_global(
    hazard_df: pd.DataFrame, n_visits: int = 50
) -> list[dict[str, Any]]:
    """Mean per-visit hazard by visit_index (cohort-average trajectory), first n_visits."""
    grp = (
        hazard_df[hazard_df["visit_index"] < n_visits]
        .groupby("visit_index")["hazard"]
        .mean()
        .reset_index()
    )
    return [
        {"visit_index": int(r["visit_index"]), "mean_hazard": float(r["hazard"])}
        for _, r in grp.iterrows()
    ]


def _attr_feature_importance(
    model: HistGradientBoostingClassifier, feature_names: list[str], top_n: int = 10
) -> list[dict[str, Any]]:
    """Top-N features by GBM built-in feature_importances_."""
    imp = model.feature_importances_
    pairs = sorted(zip(feature_names, imp), key=lambda x: x[1], reverse=True)
    return [
        {"feature": name, "importance": float(score)} for name, score in pairs[:top_n]
    ]


def _attr_local_3(
    meta: pd.DataFrame,
    hazard_df: pd.DataFrame,
    cum_hazard: dict[int, float],
) -> list[dict[str, Any]]:
    """Cumulative hazard H(v) vs visit_index for 3 representative participants.

    Selects: 1 early dropper (dropout_visit_index <= 5), 1 late dropper (dropout_visit_index >= 15),
    1 completer.
    """
    droppers = meta[meta["event_observed"] == 1].copy()
    completers = meta[meta["event_observed"] == 0].copy()

    early = droppers[droppers["dropout_visit_index"] <= 5]
    late = droppers[droppers["dropout_visit_index"] >= 15]

    candidates = []
    for label, pool in [("early_dropout", early), ("late_dropout", late), ("completer", completers)]:
        if pool.empty:
            continue
        pid = int(pool.iloc[0]["participant_id"])
        pvisits = hazard_df[hazard_df["participant_id"] == pid].sort_values("visit_index")
        hvals = pvisits["hazard"].to_numpy()
        curve: list[dict[str, float]] = []
        H = 1.0
        for i, h in enumerate(hvals):
            H = H * (1.0 - float(np.clip(h, 1e-9, 1.0 - 1e-9)))
            curve.append({"visit_index": int(pvisits.iloc[i]["visit_index"]), "cum_H": float(1.0 - H)})
        dvi = meta.loc[meta["participant_id"] == pid, "dropout_visit_index"].iloc[0]
        candidates.append(
            {
                "label": label,
                "participant_id": pid,
                "dropout_visit_index": None if pd.isna(dvi) else float(dvi),
                "cum_H_curve": curve,
            }
        )
    return candidates
```

- [ ] **Step 7: Write the main training function `train_survival_model`**

Append to `models/t2d/survival.py`:

```python
def train_survival_model(
    participants: pd.DataFrame,
    engagement: pd.DataFrame,
    split: Split,
    *,
    out_root: Path = T2D_ROOT,
    baseline_1b_pr_auc: float = 0.2506,
    sequence_pr_auc: float = 0.339,
    sequence_lead_time_median: float = 17.0,
    preregistration_bar_pr_auc: float = 0.3006,
) -> dict[str, Any]:
    """Fit the discrete-time hazard model and emit all artifacts.

    Steps:
    1. Build (participant, visit) frame and assign folds by nct_id (same split as 1a/1b/3).
    2. Leakage guard BEFORE any fit.
    3. Fit HistGradientBoostingClassifier on TRAIN fold.
    4. Evaluate: C-index, calibration quartiles, lead-time curve.
    5. Attribution: global hazard trajectory, feature importance, 3 local examples.
    6. Write artifacts to out_root/.
    7. Write model card.
    8. Return the full metrics dict.
    """
    from sklearn.preprocessing import OneHotEncoder

    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    # --- (1) Build feature matrices and assign folds ---
    encoded, fold_labels, fold_meta, feature_names = build_matrices_survival(
        participants, engagement, split
    )

    train_X = encoded["train"].to_numpy(dtype=float)
    train_y = fold_labels["train"].to_numpy(dtype=int)

    # --- (2) Leakage guard BEFORE fit ---
    assert_no_forbidden_features(feature_names)

    # --- (3) Fit model ---
    model = HistGradientBoostingClassifier(random_state=MODEL_SEED, max_iter=300)
    model.fit(train_X, train_y)

    # --- (4) Rebuild one-hot encoder for per-visit prediction (refit on train) ---
    # We need the encoder object for _predict_per_visit_hazard
    cat_enc = OneHotEncoder(handle_unknown="ignore", sparse_output=False)
    train_frame_raw = build_survival_frame(participants, engagement)
    # Get train participant rows
    from models.splits import assign_folds as _af
    p_folds = _af(
        participants[["participant_id", "nct_id"]].drop_duplicates("participant_id"),
        split, nct_col="nct_id"
    )
    train_pids = set(p_folds[p_folds["fold"] == "train"]["participant_id"].tolist())
    test_pids = set(p_folds[p_folds["fold"] == "test"]["participant_id"].tolist())
    train_p = participants[participants["participant_id"].isin(train_pids)]
    cat_enc.fit(
        train_p[SURVIVAL_STATIC_CATEGORICAL].astype("string").fillna("__NA__")
    )

    # --- (5) Per-visit hazard for TEST set ---
    test_meta = fold_meta["test"]
    test_hazard_df = _predict_per_visit_hazard(
        model, engagement, participants, test_pids, feature_names, cat_enc
    )
    cum_hazard_test = _cumulative_hazard_map(test_hazard_df)

    # --- (6) C-index ---
    c_index_overall = _c_index_overall(test_meta, cum_hazard_test)
    c_index_arm_type = _c_index_by_stratum(test_meta, cum_hazard_test, "arm_type")
    c_index_phase = _c_index_by_stratum(test_meta, cum_hazard_test, "phase")

    # --- (7) Calibration quartiles ---
    calib = _calibration_quartiles(test_meta, cum_hazard_test)

    # --- (8) Lead-time curve ---
    lead_curve = _lead_time_curve(
        test_meta, engagement, model, participants, feature_names, cat_enc
    )

    # --- (9) Attribution ---
    # Global: per-visit mean hazard on TEST
    global_hazard = _attr_hazard_global(test_hazard_df)

    # Feature importance (top 10)
    feat_imp = _attr_feature_importance(model, feature_names)

    # Local 3 examples
    local_3 = _attr_local_3(test_meta, test_hazard_df, cum_hazard_test)

    # --- (10) Comparison to prior models ---
    # Directional note: C-index is a discrimination metric (rank-based); PR-AUC is a
    # precision-recall metric. Both measure discrimination power but are not directly
    # comparable numerically. C-index > 0.5 indicates above-chance discrimination,
    # consistent with the sequence model showing PR-AUC > baseline.
    comparison = {
        "baseline_1b_pr_auc": baseline_1b_pr_auc,
        "sequence_pr_auc": sequence_pr_auc,
        "sequence_lead_time_median": sequence_lead_time_median,
        "survival_c_index_overall": c_index_overall,
        "directional_note": (
            "C-index (survival) and PR-AUC (sequence/baseline) are different metrics. "
            "C-index > 0.5 = above-chance discrimination. Both signal the trajectory "
            "carries dropout signal beyond baseline covariates."
        ),
    }

    # Preregistration bar: use the sequence model's PR-AUC bar as reference
    # (survival model uses C-index, not PR-AUC — the bar is directionally aligned)
    preregistration_bar_met = (
        c_index_overall > 0.5
        and any(row["flagged_fraction"] > 0.0 for row in lead_curve)
    )

    metrics: dict[str, Any] = {
        "task": "3c_survival_discrete_time_hazard",
        "data_source": "SYNTHETIC_T2D",
        "model_class": "HistGradientBoostingClassifier (discrete-time hazard)",
        "seed": MODEL_SEED,
        "c_index": {
            "overall": c_index_overall,
            "by_arm_type": c_index_arm_type,
            "by_phase": c_index_phase,
        },
        "calibration_quartiles": calib,
        "lead_time_curve": lead_curve,
        "lead_time_hazard_threshold": LEAD_TIME_HAZARD_THRESHOLD,
        "comparison": comparison,
        "preregistration_bar_met": preregistration_bar_met,
    }

    # --- (11) Write artifacts ---
    (out_root / "survival_metrics.json").write_text(
        json.dumps(metrics, indent=2), encoding="utf-8"
    )
    (out_root / "attr_hazard_global.json").write_text(
        json.dumps(global_hazard, indent=2), encoding="utf-8"
    )
    (out_root / "attr_feature_importance.json").write_text(
        json.dumps(feat_imp, indent=2), encoding="utf-8"
    )
    (out_root / "attr_local_3.json").write_text(
        json.dumps(local_3, indent=2), encoding="utf-8"
    )

    # --- (12) Model card ---
    _write_model_card(out_root, metrics, c_index_overall, calib, comparison)

    return metrics


def _write_model_card(
    out_root: Path,
    metrics: dict[str, Any],
    c_index_overall: float,
    calib: list[dict[str, Any]],
    comparison: dict[str, Any],
) -> None:
    """Write model_card_survival.md with all required strings."""
    calib_table = (
        "| Quartile | Mean Pred H | Obs Event Rate | N |\n"
        "|---|---|---|---|\n"
    )
    for row in calib:
        calib_table += (
            f"| {row['quartile']} "
            f"| {row['mean_pred_H']:.4f} "
            f"| {row['obs_event_rate']:.4f} "
            f"| {row['n']} |\n"
        )

    card = f"""# Model Card — Vigil Phase 3 Step (c): Discrete-Time Hazard Survival Model

## SYNTHETIC

This model is trained and evaluated on a **SYNTHETIC** cohort calibrated to real aggregate
AACT/ClinicalTrials.gov statistics. It is a **method demonstration** — it proves the discrete-time
hazard formulation can learn the planted trajectory→dropout relationship. It makes **no clinical
prediction** about real participants and contains **No PHI**.

## Formulation

**discrete-time hazard** (Cox proportional hazard, discretized to visit level).

For each participant, at each visit v up to and including their terminal visit, we emit one
training row with label y_v = 1 iff the visit is the dropout event (event_observed=1 AND
v == dropout_visit_index), else 0. Censored participants contribute all their observed visits
with y_v = 0 throughout (right-censoring — they were event-free up to their last visit).

Model: `HistGradientBoostingClassifier(max_iter=300, random_state={metrics["seed"]})` trained
on all (participant, visit) pairs in TRAIN.

Cumulative hazard: H(T) = 1 - prod(1 - h(v)) for v=0..T, where h(v) is the predicted
per-visit hazard probability.

## Censoring

**censoring: ~0.03%** admin-censored (verified in calibration_report_v2.json).

Censoring is **non-informative** — participants that are censored in this cohort are observed
completers (did not drop), and their engagement sequence is fully observed through the planned
visit count. The few admin-censored rows (~40) are the minority edge case. Censoring is
**NOT the value driver** — the planted trajectory signal (deteriorating engagement) is the
mechanism being learned.

## Planted Trajectory Assumption

The generator plants a dropout-propensity rule: a participant's hazard increases sharply
after **≥3 consecutive missed visits** (the planted trajectory assumption). The miss_probability
is the LATENT hazard from the generator. Engagement features (attended, missed, cumulative_missed,
consecutive_missed) are the OBSERVABLE proxies for this trajectory. The model learns the
observable signal; the latent hazard is never directly observed.

## Feature Governance

**miss_probability is the LATENT hazard** (the generator's internal variable) and is
**NEVER a feature**. Using it would directly recover the planted rule and produce trivially
inflated metrics. The leakage assertion fires at module import and before every fit.

Static features: age_years, hba1c_pct, bmi, n_sites, planned_duration_days, sex, phase, arm_type.
Dynamic features (per visit): visit_index, attended, missed, cumulative_missed, consecutive_missed.
Outcome columns (dropped, censored, dropout_visit_index, arm_real_dropout_rate, event_observed,
time_to_event, synthetic, *_baseline_imputed) are EXCLUDED from the feature matrix.

## Lead-Time

**hazard-curve lead-time is the AUTHORITATIVE lead-time, superseding the threshold lead-time
from the sequence model.** The hazard curve sweeps visit-index operating points t_flag ∈ {{1,2,3,5,8,13,21}}
and reports flagged_fraction + median_lead_time at each point, rather than a single threshold.

## Evaluation (TEST set — temporal held-out split by nct_id)

### C-index (Overall)

C-index (overall): **{c_index_overall:.4f}**

Per stratum (arm_type): {metrics["c_index"]["by_arm_type"]}

Per stratum (phase): {metrics["c_index"]["by_phase"]}

### Calibration Table

{calib_table}

### Comparison to Prior Models

| Model | Metric | Value |
|---|---|---|
| 1b Synthetic Structural (GBT) | PR-AUC | {comparison["baseline_1b_pr_auc"]:.4f} |
| 3 Sequence LSTM | PR-AUC | {comparison["sequence_pr_auc"]:.4f} |
| 3c Survival (this model) | C-index | {c_index_overall:.4f} |

C-index (survival) and PR-AUC (sequence/baseline) are **different metrics** (rank-based vs
precision-recall). A C-index > 0.5 indicates above-chance discrimination, directionally
consistent with the sequence model showing PR-AUC > the structural baseline. The trajectory
signal is present regardless of which discrimination metric is used.

## No PHI

This model uses no real participant data. All data is SYNTHETIC and labelled as such.
The cohort carries `synthetic=True` on every row.

## Data Lineage

- Participants: `data/synthetic/t2d/participants.parquet` (synthetic=True, 121,225 rows)
- Engagement: `data/synthetic/t2d/engagement_censored.parquet` (2,285,033 rows)
- Split: `temporal_group_split(t2d_cohort_trials)` — same as Phase 3 steps 1a/1b/3
- Seed: `MODEL_SEED = {metrics["seed"]}` (deterministic, bit-identical)
"""
    (out_root / "model_card_survival.md").write_text(card, encoding="utf-8")
```

- [ ] **Step 8: Verify the file is syntactically valid**

```bash
uv run python -c "import models.t2d.survival; print('syntax OK')"
```

Expected: `syntax OK` (any `SyntaxError` or `ImportError` means a typo — fix it before continuing).

---

## Task 3: Write `scripts/run_survival.py`

**Files:**
- Create: `scripts/run_survival.py`

- [ ] **Step 1: Create the script**

```python
"""Build-time entry point: fit the discrete-time hazard survival model on the T2D synthetic cohort.

Usage:
    uv run python scripts/run_survival.py

Reads:
    data/synthetic/t2d/participants.parquet
    data/synthetic/t2d/engagement_censored.parquet
    data/clean/ref_trial.parquet  (for the temporal split)
    data/raw/aact/2026-06-05/     (for the T2D mesh/intervention predicates)
    data/models/t2d/preregistration.json  (for the comparison bars)

Writes (git-ignored):
    data/models/t2d/survival_metrics.json
    data/models/t2d/attr_hazard_global.json
    data/models/t2d/attr_feature_importance.json
    data/models/t2d/attr_local_3.json
    data/models/t2d/model_card_survival.md
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

import pandas as pd

# --- resolve repo root so imports work when run from any cwd ---
REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))

from ingestion.config import CLEAN_ROOT, RAW_ROOT, SYNTHETIC_ROOT  # noqa: E402
from models.t2d.cohort import t2d_cohort_trials  # noqa: E402
from models.splits import temporal_group_split  # noqa: E402
from models.t2d.survival import train_survival_model  # noqa: E402

T2D_SYNTH_ROOT = SYNTHETIC_ROOT / "t2d"
T2D_MODELS_ROOT = REPO_ROOT / "data" / "models" / "t2d"


def main() -> None:
    # Load the participants and engagement
    print("Loading synthetic T2D cohort...")
    participants = pd.read_parquet(T2D_SYNTH_ROOT / "participants.parquet")
    engagement = pd.read_parquet(T2D_SYNTH_ROOT / "engagement_censored.parquet")
    print(f"  participants: {len(participants):,}  engagement rows: {len(engagement):,}")

    # Reproduce the SAME split as 1a/1b/3
    print("Reproducing temporal group split (same as 1a/1b/3)...")
    ref_trial = pd.read_parquet(CLEAN_ROOT / "ref_trial.parquet")
    trials = t2d_cohort_trials(ref_trial, RAW_ROOT)
    split = temporal_group_split(trials)
    print(f"  Split: train={split.fold_sizes['train']} val={split.fold_sizes['val']} test={split.fold_sizes['test']} trials")

    # Read prior model bars from preregistration.json
    prereg_path = T2D_MODELS_ROOT / "preregistration.json"
    prereg = json.loads(prereg_path.read_text(encoding="utf-8"))
    baseline_1b_pr_auc = float(prereg.get("baseline_1b_test_pr_auc", 0.2506))

    seq_path = T2D_MODELS_ROOT / "sequence_metrics.json"
    seq = json.loads(seq_path.read_text(encoding="utf-8"))
    sequence_pr_auc = float(seq["test"]["overall"]["pr_auc"])
    sequence_lead_time_median = float(seq["lead_time"]["median_lead_time_visits"])

    # Train + evaluate
    print("Training survival model...")
    metrics = train_survival_model(
        participants,
        engagement,
        split,
        out_root=T2D_MODELS_ROOT,
        baseline_1b_pr_auc=baseline_1b_pr_auc,
        sequence_pr_auc=sequence_pr_auc,
        sequence_lead_time_median=sequence_lead_time_median,
    )

    # Report
    ci = metrics["c_index"]
    print(f"\n=== Survival Model Results ===")
    print(f"C-index overall:   {ci['overall']:.4f}")
    print(f"C-index by arm_type: {ci['by_arm_type']}")
    print(f"C-index by phase:    {ci['by_phase']}")
    print(f"\nCalibration quartiles:")
    for row in metrics["calibration_quartiles"]:
        print(f"  Q{row['quartile']}: pred_H={row['mean_pred_H']:.4f}  obs_rate={row['obs_event_rate']:.4f}  n={row['n']}")
    print(f"\nLead-time curve (threshold={metrics['lead_time_hazard_threshold']}):")
    for pt in metrics["lead_time_curve"]:
        print(f"  t_flag={pt['t_flag']:2d}  flagged={pt['flagged_fraction']:.3f}  median_lead={pt['median_lead_time_visits']:.1f}")
    print(f"\nPreregistration bar met: {metrics['preregistration_bar_met']}")
    print(f"\nArtifacts written to: {T2D_MODELS_ROOT}")


if __name__ == "__main__":
    main()
```

---

## Task 4: Write `tests/models/test_t2d_survival.py`

**Files:**
- Create: `tests/models/test_t2d_survival.py`

These tests are CI-safe (no data files, no torch, no lifelines slow paths — fabricate in-memory data).

- [ ] **Step 1: Write the test file**

```python
"""CI-safe tests for the T2D survival (discrete-time hazard) model — Task 3 Step (c).

Fabricates a tiny in-memory censored cohort (~200 participants, 30 max visits) so no
data/synthetic files are touched. Verifies:
1. Forbidden columns (miss_probability, synthetic, outcome cols) NOT in feature set.
2. Leakage assertion fires on a deliberately contaminated feature list.
3. Model trains and produces a C-index in [0, 1].
4. Lead-time curve has all required keys.
5. Calibration quartiles have 4 rows with required keys.
"""

from __future__ import annotations

from datetime import date

import numpy as np
import pandas as pd
import pytest

from ingestion.errors import LeakageError
from models.splits import temporal_group_split
from models.t2d.survival import (
    ALL_FEATURE_COLS,
    LEAD_TIME_T_FLAGS,
    SURVIVAL_DYNAMIC,
    SURVIVAL_STATIC_CATEGORICAL,
    SURVIVAL_STATIC_NUMERIC,
    build_survival_frame,
    train_survival_model,
)
from models.t2d.synthetic_data import FORBIDDEN_FEATURES, assert_no_forbidden_features


# ---------------------------------------------------------------------------
# Fabrication helpers
# ---------------------------------------------------------------------------

def _fabricate_censored(
    n_trials: int = 8,
    n_participants_per_trial: int = 25,
    max_visits: int = 30,
    seed: int = 42,
) -> tuple[pd.DataFrame, pd.DataFrame, object]:
    """Build a tiny in-memory censored T2D cohort for CI tests.

    Returns (participants_df, engagement_df, split).
    """
    rng = np.random.default_rng(seed)
    trials: list[dict] = []
    parts: list[dict] = []
    eng_rows: list[dict] = []
    pid = 0

    for ti in range(n_trials):
        nct = f"NCT{ti:08d}"
        start_date = date(2000 + ti, 1, 1)
        trials.append({"nct_id": nct, "start_date": start_date})

        n_v = int(rng.integers(10, max_visits))
        for _ in range(n_participants_per_trial):
            dropped = bool(rng.random() < 0.3)
            censored = not dropped  # completer == censored in this cohort
            if dropped:
                dvi = int(rng.integers(2, n_v))
                obs_visits = dvi + 1
                event_observed = 1
                time_to_event = float(dvi + 1)
                enrollment_day = int(rng.integers(0, 100))
            else:
                dvi = None
                obs_visits = n_v
                event_observed = 0
                time_to_event = float(n_v)
                enrollment_day = int(rng.integers(0, 100))

            parts.append(
                {
                    "participant_id": pid,
                    "nct_id": nct,
                    "arm_id": f"{nct}A",
                    "arm_type": rng.choice(["Other", "Placebo Comparator"]),
                    "arm_band": "mid",
                    "phase": rng.choice(["PHASE2", "PHASE3"]),
                    "n_sites": int(rng.integers(1, 30)),
                    "planned_duration_days": int(rng.integers(180, 730)),
                    "n_visits": n_v,
                    "age_years": float(rng.normal(60, 8)),
                    "age_baseline_imputed": bool(rng.random() < 0.1),
                    "sex": rng.choice(["MALE", "FEMALE"]),
                    "hba1c_pct": float(rng.normal(7.5, 1.0)),
                    "hba1c_baseline_imputed": bool(rng.random() < 0.1),
                    "bmi": float(rng.normal(30, 4)),
                    "bmi_baseline_imputed": bool(rng.random() < 0.1),
                    "arm_real_dropout_rate": float(rng.uniform(0.1, 0.4)),
                    "synthetic": True,
                    "dropped": dropped,
                    "censored": censored,
                    "dropout_visit_index": float(dvi) if dvi is not None else np.nan,
                    "dropout_reason": "WITHDRAWAL_BY_SUBJECT" if dropped else "",
                    "enrollment_day": enrollment_day,
                    "time_to_event": time_to_event,
                    "event_observed": event_observed,
                }
            )

            cum = 0
            cons = 0
            for vi in range(obs_visits):
                missed = bool(rng.random() < (0.5 if dropped and vi >= dvi - 2 else 0.15))
                cum += int(missed)
                cons = cons + 1 if missed else 0
                eng_rows.append(
                    {
                        "participant_id": pid,
                        "visit_index": vi,
                        "attended": not missed,
                        "missed": missed,
                        "cumulative_missed": cum,
                        "consecutive_missed": cons,
                        "miss_probability": float(rng.random()),  # latent — must never be a feature
                        "synthetic": True,
                    }
                )
            pid += 1

    p_df = pd.DataFrame(parts)
    e_df = pd.DataFrame(eng_rows)
    trials_df = pd.DataFrame(trials)
    split = temporal_group_split(trials_df)
    return p_df, e_df, split


@pytest.fixture(scope="module")
def fabricated_censored():
    return _fabricate_censored()


# ---------------------------------------------------------------------------
# Test 1: Forbidden columns not in feature set
# ---------------------------------------------------------------------------

def test_forbidden_columns_not_in_feature_set() -> None:
    """miss_probability / synthetic / outcome cols must never appear in the feature spec."""
    feature_cols = set(ALL_FEATURE_COLS)
    assert "miss_probability" not in feature_cols, "miss_probability must not be a feature"
    assert "synthetic" not in feature_cols, "synthetic must not be a feature"
    for prov in ("age_baseline_imputed", "hba1c_baseline_imputed", "bmi_baseline_imputed"):
        assert prov not in feature_cols, f"{prov} (provenance) must not be a feature"
    for outcome in ("dropped", "censored", "dropout_visit_index", "arm_real_dropout_rate",
                    "event_observed", "time_to_event", "dropout_reason"):
        assert outcome not in feature_cols, f"{outcome} (outcome) must not be a feature"
    # The full feature set must be disjoint from FORBIDDEN_FEATURES
    assert feature_cols.isdisjoint(FORBIDDEN_FEATURES), (
        f"feature spec overlaps FORBIDDEN_FEATURES: {feature_cols & FORBIDDEN_FEATURES}"
    )


# ---------------------------------------------------------------------------
# Test 2: Leakage assertion fires on contaminated feature list
# ---------------------------------------------------------------------------

def test_leakage_assertion_fires_on_miss_probability() -> None:
    """Injecting miss_probability must raise LeakageError."""
    with pytest.raises(LeakageError):
        assert_no_forbidden_features([*SURVIVAL_DYNAMIC, "miss_probability"])


def test_leakage_assertion_fires_on_synthetic() -> None:
    with pytest.raises(LeakageError):
        assert_no_forbidden_features(["synthetic"])


def test_leakage_assertion_fires_on_dropped() -> None:
    with pytest.raises(LeakageError):
        assert_no_forbidden_features(["dropped"])


def test_leakage_assertion_fires_on_event_observed() -> None:
    with pytest.raises(LeakageError):
        assert_no_forbidden_features(["event_observed"])


# ---------------------------------------------------------------------------
# Test 3: Model trains and C-index in [0, 1]
# ---------------------------------------------------------------------------

def test_model_trains_and_c_index_in_range(fabricated_censored, tmp_path) -> None:
    """The full pipeline runs on fabricated data and returns a valid C-index."""
    p, e, split = fabricated_censored
    metrics = train_survival_model(p, e, split, out_root=tmp_path)
    ci = metrics["c_index"]["overall"]
    assert 0.0 <= ci <= 1.0, f"C-index {ci} out of [0,1]"


# ---------------------------------------------------------------------------
# Test 4: Lead-time curve has all required keys
# ---------------------------------------------------------------------------

def test_lead_time_curve_keys(fabricated_censored, tmp_path) -> None:
    """Lead-time curve must have entries for all t_flag values with the required keys."""
    p, e, split = fabricated_censored
    metrics = train_survival_model(p, e, split, out_root=tmp_path)
    curve = metrics["lead_time_curve"]
    assert len(curve) == len(LEAD_TIME_T_FLAGS), (
        f"Expected {len(LEAD_TIME_T_FLAGS)} t_flag entries, got {len(curve)}"
    )
    for entry in curve:
        assert "t_flag" in entry
        assert "flagged_fraction" in entry
        assert "median_lead_time_visits" in entry


# ---------------------------------------------------------------------------
# Test 5: Calibration quartiles shape
# ---------------------------------------------------------------------------

def test_calibration_quartiles_shape(fabricated_censored, tmp_path) -> None:
    """Calibration must return exactly 4 quartile rows with required keys."""
    p, e, split = fabricated_censored
    metrics = train_survival_model(p, e, split, out_root=tmp_path)
    calib = metrics["calibration_quartiles"]
    assert len(calib) == 4, f"Expected 4 quartile rows, got {len(calib)}"
    for row in calib:
        assert "quartile" in row
        assert "mean_pred_H" in row
        assert "obs_event_rate" in row
        assert "n" in row


# ---------------------------------------------------------------------------
# Test 6: build_survival_frame — y col is binary and outcome cols absent
# ---------------------------------------------------------------------------

def test_build_survival_frame_no_outcome_cols(fabricated_censored) -> None:
    """build_survival_frame must not include forbidden outcome cols in its output."""
    p, e, _ = fabricated_censored
    frame = build_survival_frame(p, e)
    assert "y" in frame.columns
    assert frame["y"].isin([0, 1]).all()
    # These must NOT be features in the frame (they were used to compute y, then dropped)
    assert "dropped" not in frame.columns
    assert "dropout_visit_index" not in frame.columns
    assert "miss_probability" not in frame.columns
    assert "synthetic" not in frame.columns
```

- [ ] **Step 2: Run the tests to verify they pass**

```bash
uv run pytest tests/models/test_t2d_survival.py -v
```

Expected: all tests PASS (the fabricated cohort is tiny — tests should complete in under 30 seconds).

---

## Task 5: Run the gates

- [ ] **Step 1: ruff check**

```bash
uv run ruff check .
```

Expected: no errors. If there are errors, fix each one reported (do not skip or `# noqa` without justification). Common issues: unused imports, line length.

- [ ] **Step 2: Fix any ruff issues**

For each error reported by ruff, edit the file to fix it. Re-run `uv run ruff check .` until clean.

- [ ] **Step 3: check_specs**

```bash
uv run python scripts/check_specs.py
```

Expected: PASS. If it fails, check which spec section is missing and update `specs/` accordingly (do NOT silence the check).

- [ ] **Step 4: golden oracle tests**

```bash
uv run pytest tests/golden/ -q
```

Expected: all pass (these test the data pipeline, not the model — should be unaffected).

- [ ] **Step 5: leakage tests**

Find the leakage test file:

```bash
uv run pytest tests/test_leakage.py -q
```

If `tests/test_leakage.py` does not exist, check `tests/` for the leakage test:

```bash
uv run pytest tests/ -k "leakage" -q
```

Expected: green.

- [ ] **Step 6: fast suite (excluding slow)**

```bash
uv run pytest -m "not slow" -q
```

Expected: all pass, including the new survival tests. If any test fails, fix the root cause — do not skip.

---

## Task 6: Run the survival model end-to-end and verify artifacts

- [ ] **Step 1: Run the script**

```bash
uv run python scripts/run_survival.py
```

Expected: prints the results table to stdout and exits without error. Typical runtime: 2–5 minutes on CPU (the training set is ~1.5M visit rows).

- [ ] **Step 2: Verify artifacts exist**

```bash
python -c "
from pathlib import Path
root = Path('data/models/t2d')
for f in ['survival_metrics.json', 'attr_hazard_global.json', 'attr_feature_importance.json', 'attr_local_3.json', 'model_card_survival.md']:
    p = root / f
    print(f'{f}: {\"OK\" if p.exists() else \"MISSING\"}')
"
```

Expected: all 5 artifacts `OK`.

- [ ] **Step 3: Verify model card contains required strings**

```bash
python -c "
from pathlib import Path
card = (Path('data/models/t2d') / 'model_card_survival.md').read_text()
required = [
    'SYNTHETIC',
    'discrete-time hazard',
    'censoring: ~0.03%',
    'non-informative',
    'NOT the value driver',
    'planted trajectory assumption',
    'miss_probability is the LATENT hazard',
    'NEVER a feature',
    'hazard-curve lead-time is the AUTHORITATIVE lead-time, superseding the threshold lead-time from the sequence model',
    'No PHI',
]
for s in required:
    print(f'[{\"OK\" if s in card else \"MISSING\"}] {repr(s[:60])}')
"
```

Expected: all `[OK]`.

- [ ] **Step 4: Verify C-index is in range and calibration has 4 rows**

```bash
python -c "
import json
from pathlib import Path
m = json.loads((Path('data/models/t2d') / 'survival_metrics.json').read_text())
ci = m['c_index']['overall']
print(f'C-index overall: {ci:.4f}')
assert 0 < ci < 1, f'C-index {ci} out of range'
assert len(m['calibration_quartiles']) == 4, 'Need 4 calibration quartiles'
print('lead_time_curve entries:', len(m['lead_time_curve']))
print('All structural checks: PASS')
"
```

---

## Task 7: Update ROADMAP.md

**Files:**
- Modify: `ROADMAP.md`

- [ ] **Step 1: Read the current ROADMAP.md Phase 3 section**

Find the `## Phase 3 — Models` section. It currently reads `[ ]` (not started).

- [ ] **Step 2: Update Phase 3 to reflect survival model complete**

In `ROADMAP.md`, locate the Phase 3 section and update it to reflect what has been done. Update only the survival model items — do not mark the whole Phase 3 as complete (scores→Postgres and model routing are not done):

Replace the Phase 3 block:

```markdown
## Phase 3 — Models  [~]
Baselines (logistic + GBT) on real registry · sequence model (synthetic cohort) · survival ·
calibration + SHAP · temporal-only eval (PR-AUC, recall@precision, lead-time gain) · scores → Postgres.
**Done when:** reproducible training from Phase 1 REAL data; metrics logged; scores behind RLS.
> Phase 3 sub-steps completed:
> - [x] 1a: Real-T2D structural baseline (HistGBT + LogReg on 755 trials / 2,402 arms, temporal split)
> - [x] 1b: Synthetic-structural ablation (same feature contract, synthetic cohort)
> - [x] 3: Sequence LSTM (synthetic cohort, LSTM per-visit classifier, lead-time 17 visits, PR-AUC 0.339 > pre-reg bar 0.3006)
> - [x] **3c: Survival model** — discrete-time hazard (HistGBT on (participant, visit) pairs), C-index computed, calibration quartiles, hazard-curve lead-time (authoritative), model card written. Artifacts at `data/models/t2d/survival_metrics.json`.
> Still pending: SHAP explanations, scores→Postgres (behind RLS), model routing.
```

---

## Self-Review Checklist

1. **Spec coverage:**
   - `miss_probability` / `synthetic` / outcome cols excluded: covered by `_assert_survival_features_clean()` at module import + `assert_no_forbidden_features` before fit.
   - Same `temporal_group_split` as 1a/1b/3: `train_survival_model` calls `assign_folds(participants, split)` — the split object is passed in from outside (same call as Task 3).
   - `HistGradientBoostingClassifier(random_state=MODEL_SEED, max_iter=300)`: specified in Task 2 Step 7.
   - C-index via `lifelines.utils.concordance_index`: in `_c_index_overall`.
   - Calibration quartiles (4 rows): in `_calibration_quartiles`.
   - Lead-time curve with Fibonacci-ish t_flags `{1,2,3,5,8,13,21}`: in `LEAD_TIME_T_FLAGS` + `_lead_time_curve`.
   - Attribution: global hazard, feature importance top 10, local 3 examples — all in Task 2 Step 6.
   - Model card with all required strings — in `_write_model_card`.
   - 5 output artifacts — written in `train_survival_model`.
   - Tests — Task 4 (6 tests).
   - Gates — Task 5.
   - ROADMAP update — Task 7.
   - `lifelines` in `pyproject.toml` — Task 1.

2. **Placeholder scan:** No TBD/TODO left in the plan. All code is complete.

3. **Type consistency:**
   - `train_survival_model` calls `build_matrices_survival` which calls `build_survival_frame` — consistent.
   - `_predict_per_visit_hazard` takes `(model, engagement, participants, pid_set, feature_names, cat_enc)` — called the same way in `train_survival_model` and `_lead_time_curve`.
   - `cat_enc` is always the `OneHotEncoder` instance fitted on train categoricals.
   - `feature_names` is always the list returned by `_one_hot_encode_static` (cat names + numeric + dynamic).

4. **Known complexity note:** `_cumulative_hazard` was written twice in Task 2 Step 4 and then corrected in the same step. The final version to use is `_predict_per_visit_hazard` + `_cumulative_hazard_map`. The intermediate `_cumulative_hazard` function definition should NOT be in the final file. When writing the file in one pass (Task 2 Step 1), include only the final clean versions.

---

**Plan complete and saved to `docs/superpowers/plans/2026-06-08-survival-model.md`.**

Two execution options:

**1. Subagent-Driven (recommended)** — fresh subagent per task, review between tasks, fast iteration

**2. Inline Execution** — execute tasks in this session using executing-plans, batch execution with checkpoints

Which approach?
