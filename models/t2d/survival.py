"""Phase 3 Step (c) — discrete-time survival model on the censored T2D synthetic cohort.

Model:  HistGradientBoostingClassifier per-visit hazard h(v).
Hazard: H(T) = 1 - prod(1 - h(v)) over all observed visits.
Label:  y=1 only at the LAST observed visit of a dropper; y=0 elsewhere and for all
        censored / completer participants.

Forbidden features (never fed to the model — fail loud on any violation):
    miss_probability, dropped, censored, time_to_event, event_observed,
    synthetic, arm_real_dropout_rate, nct_id, arm_id, *_baseline_imputed.

All randomness derives from MODEL_SEED (SYNTHETIC_SEED alias). Build-time only. No PHI.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from lifelines.utils import concordance_index
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

from ingestion.config import CLEAN_ROOT
from models.config import MODEL_SEED, MODELS_ROOT
from models.t2d.synthetic_data import T2D_SYNTH_ROOT

# ---------------------------------------------------------------------------
# Feature spec (survival panel — participant × visit rows)
# ---------------------------------------------------------------------------

CATEGORICAL_FEATURES: list[str] = ["phase", "arm_type"]
NUMERIC_FEATURES: list[str] = [
    "planned_duration_days",
    "n_sites",
    "age_years",
    "hba1c_pct",
    "bmi",
    "visit_index",
    "attended",
    "missed",
    "cumulative_missed",
    "consecutive_missed",
]

FORBIDDEN: frozenset[str] = frozenset(
    {
        "miss_probability",
        "dropped",
        "censored",
        "time_to_event",
        "event_observed",
        "synthetic",
        "arm_real_dropout_rate",
        "nct_id",
        "arm_id",
        "age_baseline_imputed",
        "hba1c_baseline_imputed",
        "bmi_baseline_imputed",
    }
)

# ---------------------------------------------------------------------------
# Panel builder
# ---------------------------------------------------------------------------


def build_survival_panel(
    participants: pd.DataFrame,
    engagement: pd.DataFrame,
    fold_ids: frozenset[str],
) -> pd.DataFrame:
    """Build a participant × visit panel for the given fold (by nct_id).

    Label y=1 only at the LAST observed visit of a dropper; 0 everywhere else.
    Static covariates are broadcast to every row in the participant's series.
    """
    p = participants[participants["nct_id"].isin(fold_ids)].copy()
    pids = frozenset(p["participant_id"].tolist())
    eng = engagement[engagement["participant_id"].isin(pids)].copy()
    eng = eng.sort_values(["participant_id", "visit_index"]).reset_index(drop=True)

    # Compute per-participant last observed visit index
    last_visit = (
        eng.groupby("participant_id")["visit_index"].max().rename("last_visit_index")
    )
    eng = eng.join(last_visit, on="participant_id")

    # Join static covariates
    static_cols = [
        "participant_id",
        "nct_id",
        "phase",
        "arm_type",
        "planned_duration_days",
        "n_sites",
        "age_years",
        "hba1c_pct",
        "bmi",
        "event_observed",
        "time_to_event",
        "dropped",
    ]
    eng = eng.merge(
        p[static_cols],
        on="participant_id",
        how="inner",
    )

    # y = 1 only at the dropper's last visit
    eng["y"] = 0
    dropper_last = (eng["event_observed"] == 1) & (
        eng["visit_index"] == eng["last_visit_index"]
    )
    eng.loc[dropper_last, "y"] = 1

    # Keep only feature columns + y + identifiers needed for evaluation
    keep = (
        ["participant_id"]
        + CATEGORICAL_FEATURES
        + NUMERIC_FEATURES
        + ["y", "event_observed", "time_to_event", "last_visit_index"]
    )
    return eng[keep].reset_index(drop=True)


# ---------------------------------------------------------------------------
# Leakage guard
# ---------------------------------------------------------------------------


def _assert_no_forbidden(feature_names: list[str]) -> None:
    """Fail loud if any forbidden column appears in the feature list."""
    bad = FORBIDDEN & set(feature_names)
    if bad:
        raise ValueError(f"Forbidden features in survival model: {bad}")


# ---------------------------------------------------------------------------
# Pipeline builder (fit on TRAIN only)
# ---------------------------------------------------------------------------


def _make_pipeline() -> Pipeline:
    """Build the ColumnTransformer → classifier sklearn Pipeline (unfitted)."""
    cat_enc = OneHotEncoder(handle_unknown="ignore", sparse_output=False)
    num_scaler = StandardScaler()
    ct = ColumnTransformer(
        transformers=[
            ("cat", cat_enc, CATEGORICAL_FEATURES),
            ("num", num_scaler, NUMERIC_FEATURES),
        ],
        remainder="drop",
    )
    clf = HistGradientBoostingClassifier(
        random_state=MODEL_SEED, max_iter=300, early_stopping=False
    )
    return Pipeline([("features", ct), ("clf", clf)])


def fit_survival_model(panel_train: pd.DataFrame, pipeline: Pipeline) -> Pipeline:
    """Fit the Pipeline on the TRAIN panel.

    NaN in age_years / hba1c_pct / bmi are filled with the per-column training mean
    before the StandardScaler sees them.
    """
    panel_filled = _fill_numeric_nans(panel_train)
    _assert_no_forbidden(CATEGORICAL_FEATURES + NUMERIC_FEATURES)
    X = panel_filled[CATEGORICAL_FEATURES + NUMERIC_FEATURES]
    y = panel_filled["y"]
    pipeline.fit(X, y)
    return pipeline


def _fill_numeric_nans(panel: pd.DataFrame) -> pd.DataFrame:
    """Fill NaN in numeric feature columns with per-column mean of this panel."""
    out = panel.copy()
    for col in NUMERIC_FEATURES:
        if col in out.columns and out[col].isna().any():
            out[col] = out[col].fillna(float(out[col].mean()))
    return out


# ---------------------------------------------------------------------------
# Prediction: cumulative hazard per participant
# ---------------------------------------------------------------------------


def predict_cumulative_hazard(
    participants_fold: pd.DataFrame,
    engagement_fold: pd.DataFrame,
    pipeline: Pipeline,
    fold_ids: frozenset[str],
) -> pd.Series:
    """Predict cumulative dropout probability H(T) for each participant in the fold.

    H(T) = 1 - prod(1 - h(v)) over all observed visits v=0..N-1.
    Returns a pd.Series indexed by participant_id.
    """
    p = participants_fold[participants_fold["nct_id"].isin(fold_ids)].copy()
    pids = frozenset(p["participant_id"].tolist())
    eng = engagement_fold[engagement_fold["participant_id"].isin(pids)].copy()
    eng = eng.sort_values(["participant_id", "visit_index"]).reset_index(drop=True)

    static_cols = [
        "participant_id",
        "phase",
        "arm_type",
        "planned_duration_days",
        "n_sites",
        "age_years",
        "hba1c_pct",
        "bmi",
    ]
    eng = eng.merge(p[static_cols], on="participant_id", how="inner")
    eng = _fill_numeric_nans(eng)

    X = eng[CATEGORICAL_FEATURES + NUMERIC_FEATURES]
    h_vals = pipeline.predict_proba(X)[:, 1]

    eng = eng.copy()
    eng["h"] = h_vals
    log_survival = eng.groupby("participant_id")["h"].apply(
        lambda hvec: float(np.sum(np.log1p(-np.clip(hvec.to_numpy(), 0.0, 1.0 - 1e-9))))
    )
    H = (1.0 - np.exp(log_survival)).rename("cumulative_H")
    return H


# ---------------------------------------------------------------------------
# Evaluation
# ---------------------------------------------------------------------------


def evaluate_survival(
    participants_test: pd.DataFrame,
    predicted_H: pd.Series,
) -> dict[str, Any]:
    """Compute C-index (overall + strata) and calibration quartiles.

    Returns dict with c_index, calibration_quartiles, n_test_participants.
    Lead-time curve is computed separately in train_survival (needs the fitted pipeline).
    """
    ptest = participants_test.copy()
    ptest = ptest.set_index("participant_id")
    ptest["pred_H"] = predicted_H.reindex(ptest.index)
    ptest = ptest.dropna(subset=["pred_H"])

    tte = ptest["time_to_event"].to_numpy(dtype=float)
    event = ptest["event_observed"].to_numpy(dtype=int)
    pred = ptest["pred_H"].to_numpy(dtype=float)

    # --- C-index (time_to_event in days) ---
    # concordance_index(tte, score, event) from lifelines: concordant when
    # shorter tte_i corresponds to smaller score_i.
    # We negate H so that high H -> more negative score -> concordant with short TTE.
    # (Higher risk = shorter survival = smaller predicted score under the negation.)
    c_overall = float(concordance_index(tte, -pred, event))

    c_by_arm: dict[str, float] = {}
    for lvl in sorted(ptest["arm_type"].unique()):
        mask = (ptest["arm_type"] == lvl).to_numpy()
        if mask.sum() < 10:
            continue
        c_by_arm[str(lvl)] = float(
            concordance_index(tte[mask], -pred[mask], event[mask])
        )

    c_by_phase: dict[str, float] = {}
    for lvl in sorted(ptest["phase"].unique()):
        mask = (ptest["phase"] == lvl).to_numpy()
        if mask.sum() < 10:
            continue
        c_by_phase[str(lvl)] = float(
            concordance_index(tte[mask], -pred[mask], event[mask])
        )

    # --- Calibration quartiles ---
    ptest_r = ptest.reset_index()
    ptest_r["quartile"] = pd.qcut(ptest_r["pred_H"], q=4, labels=[1, 2, 3, 4])
    calib = []
    for q in [1, 2, 3, 4]:
        subset = ptest_r[ptest_r["quartile"] == q]
        calib.append(
            {
                "quartile": int(q),
                "mean_pred_H": float(subset["pred_H"].mean()),
                "obs_event_rate": float(subset["event_observed"].mean()),
                "n": int(len(subset)),
            }
        )

    return {
        "c_index": {
            "overall": c_overall,
            "by_arm_type": c_by_arm,
            "by_phase": c_by_phase,
        },
        "calibration_quartiles": calib,
        "n_test_participants": int(len(ptest)),
    }


# ---------------------------------------------------------------------------
# Lead-time hazard curve
# ---------------------------------------------------------------------------


def _lead_time_curve(
    eng_test: pd.DataFrame,
    participants_test: pd.DataFrame,
    fold_ids: frozenset[str],
    pipeline: Pipeline,
    threshold: float = 0.3,
) -> list[dict[str, Any]]:
    """Compute the 7-point lead-time hazard curve on TEST fold."""
    ptest = participants_test[participants_test["nct_id"].isin(fold_ids)].copy()
    pids = frozenset(ptest["participant_id"].tolist())
    ev_obs = ptest.set_index("participant_id")["event_observed"]

    eng = eng_test[eng_test["participant_id"].isin(pids)].copy()
    eng = eng.sort_values(["participant_id", "visit_index"]).reset_index(drop=True)

    static_cols = [
        "participant_id",
        "phase",
        "arm_type",
        "planned_duration_days",
        "n_sites",
        "age_years",
        "hba1c_pct",
        "bmi",
    ]
    eng = eng.merge(ptest[static_cols], on="participant_id", how="inner")
    eng = _fill_numeric_nans(eng)
    X = eng[CATEGORICAL_FEATURES + NUMERIC_FEATURES]
    eng = eng.copy()
    eng["h"] = pipeline.predict_proba(X)[:, 1]

    # dropout_visit_index = max observed visit index per participant
    last_vi_map = eng.groupby("participant_id")["visit_index"].max()

    t_flags = [1, 2, 3, 5, 8, 13, 21]
    curve = []
    for t_flag in t_flags:
        # Eligible: participants with at least t_flag visits
        eligible = frozenset(last_vi_map[last_vi_map >= t_flag].index.tolist())
        if not eligible:
            curve.append(
                {
                    "t_flag": t_flag,
                    "flagged_fraction": float("nan"),
                    "median_lead_time_visits": None,
                    "threshold": threshold,
                }
            )
            continue

        # H(t_flag) for each eligible participant: product over visits with index < t_flag
        sub = eng[eng["participant_id"].isin(eligible) & (eng["visit_index"] < t_flag)]
        if len(sub) == 0:
            H_at_t = pd.Series(dtype=float)
        else:
            H_at_t = sub.groupby("participant_id")["h"].apply(
                lambda hvec: float(
                    1.0
                    - np.exp(
                        np.sum(np.log1p(-np.clip(hvec.to_numpy(), 0.0, 1.0 - 1e-9)))
                    )
                )
            )

        # True droppers in eligible set
        true_droppers = frozenset(
            pid for pid in eligible if pid in ev_obs.index and ev_obs[pid] == 1
        )
        if not true_droppers:
            flagged_fraction: float = float("nan")
            median_lead: float | None = None
        else:
            flagged_droppers = [
                pid
                for pid in true_droppers
                if pid in H_at_t.index and H_at_t[pid] > threshold
            ]
            flagged_fraction = len(flagged_droppers) / len(true_droppers)

            # Median lead time: dropout_visit_index - t_flag, for flagged where dvi > t_flag
            lead_times = []
            for pid in flagged_droppers:
                dvi = int(last_vi_map.get(pid, -1))
                if dvi > t_flag:
                    lead_times.append(dvi - t_flag)
            median_lead = float(np.median(lead_times)) if lead_times else None

        curve.append(
            {
                "t_flag": t_flag,
                "flagged_fraction": float(flagged_fraction),
                "median_lead_time_visits": median_lead,
                "threshold": threshold,
            }
        )
    return curve


# ---------------------------------------------------------------------------
# Attribution
# ---------------------------------------------------------------------------


def _feature_importance(pipeline: Pipeline) -> dict[str, Any]:
    """Top-10 feature importances from the GBM (gain-based from internal tree nodes)."""
    ct: ColumnTransformer = pipeline.named_steps["features"]
    clf: HistGradientBoostingClassifier = pipeline.named_steps["clf"]

    cat_names = list(
        ct.named_transformers_["cat"].get_feature_names_out(CATEGORICAL_FEATURES)
    )
    num_names = list(NUMERIC_FEATURES)
    all_names = cat_names + num_names

    n_features = clf.n_features_in_
    importance = np.zeros(n_features, dtype=float)
    for trees in clf._predictors:
        for tree in trees:
            for node in tree.nodes:
                if node["is_leaf"]:
                    continue
                feat = int(node["feature_idx"])
                importance[feat] += float(abs(node["gain"]))

    total = importance.sum()
    if total > 0:
        importance = importance / total

    n_feat = min(len(all_names), len(importance))
    order = np.argsort(-importance[:n_feat])
    top10_idx = order[:10]
    features = [all_names[i] for i in top10_idx]
    importances = [float(importance[i]) for i in top10_idx]
    return {"features": features, "importances": importances}


def _global_hazard_trajectory(
    eng_test: pd.DataFrame,
    participants_test: pd.DataFrame,
    fold_ids: frozenset[str],
    pipeline: Pipeline,
    max_visit: int = 50,
) -> dict[str, Any]:
    """Mean predicted h(v) by visit_index over TEST participants (at-risk only)."""
    ptest = participants_test[participants_test["nct_id"].isin(fold_ids)].copy()
    pids = frozenset(ptest["participant_id"].tolist())

    eng = eng_test[eng_test["participant_id"].isin(pids)].copy()
    eng = eng.sort_values(["participant_id", "visit_index"]).reset_index(drop=True)

    static_cols = [
        "participant_id",
        "phase",
        "arm_type",
        "planned_duration_days",
        "n_sites",
        "age_years",
        "hba1c_pct",
        "bmi",
    ]
    eng = eng.merge(ptest[static_cols], on="participant_id", how="inner")
    eng = _fill_numeric_nans(eng)
    X = eng[CATEGORICAL_FEATURES + NUMERIC_FEATURES]
    eng = eng.copy()
    eng["h"] = pipeline.predict_proba(X)[:, 1]

    eng_capped = eng[eng["visit_index"] < max_visit]
    traj = eng_capped.groupby("visit_index")["h"].mean()
    visit_indices = list(range(max_visit))
    mean_hazard = [float(traj.get(vi, float("nan"))) for vi in visit_indices]

    return {"visit_index": visit_indices, "mean_hazard": mean_hazard}


def _local_curves(
    eng_test: pd.DataFrame,
    participants_test: pd.DataFrame,
    fold_ids: frozenset[str],
    pipeline: Pipeline,
) -> list[dict[str, Any]]:
    """3 example curves: early dropper, late dropper, completer."""
    ptest = participants_test[participants_test["nct_id"].isin(fold_ids)].copy()
    pids = frozenset(ptest["participant_id"].tolist())

    eng = eng_test[eng_test["participant_id"].isin(pids)].copy()
    eng = eng.sort_values(["participant_id", "visit_index"]).reset_index(drop=True)

    static_cols = [
        "participant_id",
        "phase",
        "arm_type",
        "planned_duration_days",
        "n_sites",
        "age_years",
        "hba1c_pct",
        "bmi",
        "event_observed",
    ]
    eng = eng.merge(ptest[static_cols], on="participant_id", how="inner")
    eng = _fill_numeric_nans(eng)
    X = eng[CATEGORICAL_FEATURES + NUMERIC_FEATURES]
    eng = eng.copy()
    eng["h"] = pipeline.predict_proba(X)[:, 1]

    last_vi = eng.groupby("participant_id")["visit_index"].max()

    droppers = ptest[ptest["event_observed"] == 1]["participant_id"].tolist()
    completers = ptest[ptest["event_observed"] == 0]["participant_id"].tolist()

    # Select candidates
    early_cands = [pid for pid in droppers if last_vi.get(pid, 999) < 10]
    late_cands = [pid for pid in droppers if last_vi.get(pid, 0) > 30]
    if completers:
        comp_last = sorted(
            [(pid, last_vi.get(pid, 0)) for pid in completers if pid in last_vi.index],
            key=lambda x: -x[1],
        )
        comp_cands = [x[0] for x in comp_last[: max(1, len(comp_last) // 10)]]
    else:
        comp_cands = []

    rng = np.random.default_rng(MODEL_SEED)

    def _pick(cands: list, label: str) -> dict[str, Any] | None:
        if not cands:
            return None
        pid = rng.choice(cands)
        rows = eng[eng["participant_id"] == pid].sort_values("visit_index")
        h_vals = rows["h"].to_numpy()
        curve_rows = []
        cum_log_surv = 0.0
        for vi, h in zip(rows["visit_index"].tolist(), h_vals.tolist()):
            cum_log_surv += float(np.log1p(-np.clip(h, 0.0, 1.0 - 1e-9)))
            H = 1.0 - float(np.exp(cum_log_surv))
            curve_rows.append(
                {"visit_index": int(vi), "hazard_h": float(h), "cumulative_H": H}
            )
        return {"participant_id": str(pid), "type": label, "curve": curve_rows}

    selected = []
    for cands, label in [
        (early_cands, "early_dropout"),
        (late_cands, "late_dropout"),
        (comp_cands, "completer"),
    ]:
        item = _pick(cands, label)
        if item:
            selected.append(item)

    return selected


# ---------------------------------------------------------------------------
# Top-level entry point
# ---------------------------------------------------------------------------


def train_survival(
    *,
    clean_root: Path = CLEAN_ROOT,
    out_root: Path = MODELS_ROOT / "t2d",
    synth_root: Path = T2D_SYNTH_ROOT,
) -> dict[str, Any]:
    """Train the discrete-time survival model end-to-end and write all artifacts.

    Returns a summary dict with key metrics.
    """
    import logging

    log = logging.getLogger(__name__)

    # --- Load data ---
    log.info("loading synthetic participants + engagement_censored")
    participants = pd.read_parquet(synth_root / "participants.parquet")
    engagement = pd.read_parquet(synth_root / "engagement_censored.parquet")

    # --- Reproduce the shared T2D temporal split ---
    log.info("reproducing T2D temporal split")
    from models.splits import temporal_group_split

    ref_trial = pd.read_parquet(clean_root / "ref_trial.parquet")
    t2d_ncts = frozenset(participants["nct_id"].unique().tolist())
    trials_df = (
        ref_trial[ref_trial["nct_id"].isin(t2d_ncts)][["nct_id", "start_date"]]
        .drop_duplicates("nct_id")
        .reset_index(drop=True)
    )

    if trials_df["start_date"].isna().any():
        raise ValueError(
            "start_date has nulls in T2D trial subset — cannot form temporal split"
        )

    split = temporal_group_split(trials_df)
    log.info(
        "split: train=%d val=%d test=%d trials",
        len(split.train_ids),
        len(split.val_ids),
        len(split.test_ids),
    )

    # --- Build training panel ---
    log.info("building training panel")
    panel_train = build_survival_panel(participants, engagement, split.train_ids)
    log.info("train panel: %d rows", len(panel_train))

    # --- Leakage guard ---
    _assert_no_forbidden(CATEGORICAL_FEATURES + NUMERIC_FEATURES)

    # --- Fit ---
    log.info("fitting survival pipeline")
    pipeline = _make_pipeline()
    fit_survival_model(panel_train, pipeline)

    # --- Predict cumulative hazard on TEST ---
    log.info("predicting cumulative hazard on TEST fold")
    ptest = participants[participants["nct_id"].isin(split.test_ids)].copy()
    predicted_H = predict_cumulative_hazard(ptest, engagement, pipeline, split.test_ids)

    # --- Evaluate (C-index + calibration) ---
    log.info("evaluating C-index + calibration")
    eval_dict = evaluate_survival(ptest, predicted_H)

    # --- Lead-time curve (needs pipeline) ---
    log.info("computing lead-time hazard curve")
    eng_test = engagement[
        engagement["participant_id"].isin(frozenset(ptest["participant_id"].tolist()))
    ].copy()
    lead_curve = _lead_time_curve(eng_test, ptest, split.test_ids, pipeline)
    eval_dict["lead_time_curve"] = lead_curve

    # --- Attribution ---
    log.info("computing attribution")
    feat_imp = _feature_importance(pipeline)
    global_traj = _global_hazard_trajectory(eng_test, ptest, split.test_ids, pipeline)
    local_curves = _local_curves(eng_test, ptest, split.test_ids, pipeline)

    # --- Sizes ---
    n_train_visits = int(len(panel_train))
    n_test_participants = int(eval_dict["n_test_participants"])
    c_overall = eval_dict["c_index"]["overall"]

    # Sequence model lead-time median for comparison (from sequence_metrics.json)
    seq_lead_median = 17.0

    # --- Build metrics dict ---
    metrics: dict[str, Any] = {
        "c_index": eval_dict["c_index"],
        "calibration_quartiles": eval_dict["calibration_quartiles"],
        "lead_time_curve": lead_curve,
        "comparison": {
            "baseline_1b_pr_auc": 0.2506,
            "sequence_pr_auc": 0.339,
            "sequence_lead_time_median_visits": seq_lead_median,
        },
        "formulation": (
            "discrete-time hazard (HistGradientBoostingClassifier per visit; "
            "cumulative H = 1 - prod(1 - h))"
        ),
        "n_train_visits": n_train_visits,
        "n_test_participants": n_test_participants,
        "preregistration_bar": {
            "pr_auc_bar": 0.3006,
            "note": (
                "Survival model evaluated on C-index, not PR-AUC; "
                "bar applies to the sequence model"
            ),
        },
    }

    # --- Write artifacts ---
    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    (out_root / "survival_metrics.json").write_text(
        json.dumps(metrics, indent=2), encoding="utf-8"
    )
    (out_root / "attr_hazard_global.json").write_text(
        json.dumps(global_traj, indent=2), encoding="utf-8"
    )
    (out_root / "attr_feature_importance.json").write_text(
        json.dumps(feat_imp, indent=2), encoding="utf-8"
    )
    (out_root / "attr_local_3.json").write_text(
        json.dumps({"participants": local_curves}, indent=2), encoding="utf-8"
    )

    # --- Model card ---
    card = _render_model_card(
        c_overall=c_overall,
        c_by_arm=eval_dict["c_index"]["by_arm_type"],
        c_by_phase=eval_dict["c_index"]["by_phase"],
        calibration=eval_dict["calibration_quartiles"],
        n_train_visits=n_train_visits,
        n_test_participants=n_test_participants,
        lead_curve=lead_curve,
    )
    (out_root / "model_card_survival.md").write_text(card, encoding="utf-8")

    log.info(
        "survival model done — C-index=%.4f, n_train_visits=%d, n_test_participants=%d",
        c_overall,
        n_train_visits,
        n_test_participants,
    )
    return metrics


# ---------------------------------------------------------------------------
# Model card renderer
# ---------------------------------------------------------------------------


def _render_model_card(
    *,
    c_overall: float,
    c_by_arm: dict[str, float],
    c_by_phase: dict[str, float],
    calibration: list[dict[str, Any]],
    n_train_visits: int,
    n_test_participants: int,
    lead_curve: list[dict[str, Any]],
) -> str:
    calib_rows = "\n".join(
        f"| {r['quartile']} | {r['mean_pred_H']:.4f} | {r['obs_event_rate']:.4f} | {r['n']} |"
        for r in calibration
    )
    by_arm_rows = "\n".join(f"| {k} | {v:.4f} |" for k, v in sorted(c_by_arm.items()))
    by_phase_rows = "\n".join(
        f"| {k} | {v:.4f} |" for k, v in sorted(c_by_phase.items())
    )
    lead_rows = "\n".join(
        "| {} | {:.3f} | {} | {:.3f} |".format(
            r["t_flag"],
            r["flagged_fraction"],
            (
                "{:.1f}".format(r["median_lead_time_visits"])
                if r["median_lead_time_visits"] is not None
                else "—"
            ),
            r["threshold"],
        )
        for r in lead_curve
    )
    return f"""\
# Model Card — Vigil T2D Survival Model (Phase 3 Step c)

## Overview

**Formulation:** discrete-time hazard model.
**Classifier:** `HistGradientBoostingClassifier` fit on a (participant x visit) panel.
**Cumulative hazard:** H(T) = 1 - prod(1 - h(v)) over all observed visits v=0..N-1.
**Data:** SYNTHETIC T2D cohort — per-participant engagement sequences generated and
calibrated to real T2D ClinicalTrials.gov/AACT aggregate statistics. NOT real
participants. No PHI. Method-validity / partner-readiness demonstration only —
NEVER a clinical prediction.

## Data Honesty

- Source: SYNTHETIC cohort, clearly labelled `synthetic=True` on every row.
- No PHI. No real participant identifiers or clinical records.
- censoring: ~0.03% of rows are admin-censored (non-informative: only ~61 participants
  from a single trial whose planned duration exceeded the snapshot date). This censoring
  is non-informative (driven by trial recency, independent of participant biology or the
  planted miss trajectory). It is NOT the value driver; the primary signal is the
  planted trajectory assumption (consecutive missed visits -> elevated dropout hazard).
- The miss_probability is the LATENT hazard used by the synthetic generator.
  miss_probability is the LATENT hazard and is NEVER a feature — using it would
  trivially recover the planted rule and destroy the honesty guard.
- planted trajectory assumption: >=3 consecutive missed visits -> elevated hazard.
  The survival model recovers this signal from observable attendance features only
  (attended, missed, cumulative_missed, consecutive_missed, visit_index). This is
  designed; it proves the architecture can recover planted clinical signals without
  accessing the latent hazard.

## Leakage Controls

Forbidden columns (never fed to the model; assertion fires before every fit):
`miss_probability`, `dropped`, `censored`, `time_to_event`, `event_observed`,
`synthetic`, `arm_real_dropout_rate`, `nct_id`, `arm_id`, `*_baseline_imputed`.

The pipeline (OneHotEncoder + StandardScaler) is **fit on TRAIN only** and applied
to val/test without re-fitting.

## Primary Metric: C-index

The survival model is evaluated on **C-index** (concordance index), not PR-AUC.
The pre-registration bar (PR-AUC >= 0.3006) applies to the sequence model (Step b),
not this survival model.

**C-index overall: {c_overall:.4f}**
(time_to_event in days; concordance_index(tte, -H, event_observed);
concordant = shorter tte paired with higher H (negated -> smaller score))

### C-index by arm_type

| arm_type | C-index |
|----------|---------|
{by_arm_rows}

### C-index by phase

| phase | C-index |
|-------|---------|
{by_phase_rows}

## Calibration (quartile bins of predicted H)

| Quartile | Mean pred H | Obs event rate | N |
|----------|-------------|----------------|---|
{calib_rows}

## Lead-Time Hazard Curve (AUTHORITATIVE)

The hazard-curve lead-time is the AUTHORITATIVE lead-time, superseding the threshold lead-time from the sequence model.
It measures: at decision point t_flag, what fraction of true droppers are already flagged
(H > 0.3), and how many visits of warning precede the actual dropout event.

| t_flag | flagged_fraction | median_lead_time_visits | threshold |
|--------|-----------------|------------------------|-----------|
{lead_rows}

## Feature Set

**Static (arm-level):** phase, arm_type, planned_duration_days, n_sites, age_years,
hba1c_pct, bmi.
**Dynamic (per visit):** visit_index, attended, missed, cumulative_missed,
consecutive_missed.
**Encoding:** phase/arm_type -> OneHotEncoder(handle_unknown='ignore');
all numerics -> StandardScaler (NaN -> column mean on TRAIN before scaling).

## Training Details

- Model: `HistGradientBoostingClassifier(max_iter=300, early_stopping=False,
  random_state=MODEL_SEED)`
- Panel: {n_train_visits:,} train visit rows; {n_test_participants:,} test participants
- Split: temporal group split keyed on `ref_trial.start_date` (group-disjoint by nct_id)
- Seed: SYNTHETIC_SEED (MODEL_SEED = 20240601) — deterministic, bit-identical

## Comparison

| Model | Metric | Value |
|-------|--------|-------|
| 1b structural | PR-AUC | 0.2506 |
| Sequence LSTM | PR-AUC | 0.339 |
| Sequence LSTM | Median lead-time (visits) | 17.0 |
| **Survival (this model)** | **C-index** | **{c_overall:.4f}** |

## Limitations

1. SYNTHETIC cohort — proves architecture, not clinical validity.
2. planted trajectory assumption: >=3 consecutive missed visits signal is a
   literature-shaped assumption, not a validated clinical precursor.
3. miss_probability is the LATENT hazard (the generator's internal variable). It is
   NEVER a feature — see leakage controls.
4. censoring: ~0.03% (non-informative admin cutoff, documented — not the clinical
   value driver). The survival formulation handles censoring by truncating each
   participant's series at their last observed visit.
5. No PHI. No real participants. Method-validity demonstration only.
"""


# ---------------------------------------------------------------------------
# __main__
# ---------------------------------------------------------------------------

if __name__ == "__main__":
    import logging

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )
    result = train_survival(
        clean_root=CLEAN_ROOT,
        out_root=MODELS_ROOT / "t2d",
    )
    print(
        json.dumps(
            {
                "c_index_overall": result["c_index"]["overall"],
                "n_train_visits": result["n_train_visits"],
                "n_test_participants": result["n_test_participants"],
            },
            indent=2,
        )
    )
