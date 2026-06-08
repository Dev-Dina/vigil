"""TASK 1b — the SYNTHETIC structural-ONLY baseline (the ablation = the bar to beat).

Same model class as 1a (HistGradientBoostingClassifier for a participant-level probability +
LogisticRegression) on the SYNTHETIC T2D cohort, **baseline covariates ONLY** — NO engagement /
trajectory features. Features = age, sex, hba1c_pct, bmi, phase, arm_type, n_sites,
planned_duration_days. Target = the terminal participant-level dropout label (``dropped``);
non-droppers (``censored``, observed completers — see ``synthetic_data`` docstring) are the
negatives, NOT excluded, because none is right-censored before a knowable outcome.

Same SINGLE temporal split; synthetic participants are assigned to folds by their ``nct_id``.
Encoder/scaler fit on TRAIN ONLY. NO rebalancing. A forbidden-feature assertion fires before fit.
Writes ``data/models/t2d/baseline_synthetic_structural.json`` + plots.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import numpy as np  # noqa: E402
import pandas as pd  # noqa: E402
from sklearn.ensemble import HistGradientBoostingClassifier  # noqa: E402
from sklearn.linear_model import LogisticRegression  # noqa: E402
from sklearn.preprocessing import OneHotEncoder  # noqa: E402

from models.config import MODEL_SEED, MODELS_ROOT  # noqa: E402
from models.plots import plot_calibration_curve, plot_pr_curve  # noqa: E402
from models.splits import Split  # noqa: E402
from models.t2d.metrics import classifier_panel, clip01, per_stratum  # noqa: E402
from models.t2d.synthetic_data import (  # noqa: E402
    LABEL_COL,
    STATIC_CATEGORICAL,
    STATIC_NUMERIC,
    STRATA,
    SyntheticT2D,
    assert_no_forbidden_features,
)

T2D_ROOT = MODELS_ROOT / "t2d"


def _fold_of(nct: pd.Series, split: Split) -> pd.Series:
    fold = pd.Series("", index=nct.index, dtype="object")
    fold[nct.isin(split.train_ids)] = "train"
    fold[nct.isin(split.val_ids)] = "val"
    fold[nct.isin(split.test_ids)] = "test"
    return fold


def _encode(
    rows: pd.DataFrame,
    encoder: OneHotEncoder,
    means: dict[str, float],
    stds: dict[str, float],
) -> tuple[np.ndarray, list[str]]:
    cat = encoder.transform(rows[STATIC_CATEGORICAL].astype("string").fillna("__NA__"))
    cat_names = list(encoder.get_feature_names_out(STATIC_CATEGORICAL))
    num_blocks = []
    for col in STATIC_NUMERIC:
        raw = pd.to_numeric(rows[col], errors="coerce").to_numpy(dtype=float)
        num_blocks.append(((raw - means[col]) / stds[col]).reshape(-1, 1))
    num = np.hstack(num_blocks)
    X = np.hstack([cat, num])
    names = cat_names + list(STATIC_NUMERIC)
    return X, names


def train_synthetic_structural(
    synth: SyntheticT2D,
    split: Split,
    *,
    out_root: Path = T2D_ROOT,
) -> dict[str, Any]:
    """Train + evaluate the structural-only ablation; write JSON + plots. Returns the metrics."""
    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)

    p = synth.participants
    fold = _fold_of(p["nct_id"], split)
    if (fold == "").any():
        # a synthetic nct_id outside the split would be silent drift — fail loud
        orphans = p.loc[fold == "", "nct_id"].unique()[:5]
        raise ValueError(f"synthetic participants with nct_id outside the split: {orphans}")
    framed = p.assign(fold=fold)
    train = framed[framed.fold == "train"]
    val = framed[framed.fold == "val"]
    test = framed[framed.fold == "test"]

    # Fit encoder + scaler on TRAIN ONLY.
    encoder = OneHotEncoder(handle_unknown="ignore", sparse_output=False)
    encoder.fit(train[STATIC_CATEGORICAL].astype("string").fillna("__NA__"))
    means = {c: float(pd.to_numeric(train[c], errors="coerce").mean()) for c in STATIC_NUMERIC}
    stds = {}
    for c in STATIC_NUMERIC:
        s = float(pd.to_numeric(train[c], errors="coerce").std(ddof=0))
        stds[c] = s if s > 0 else 1.0

    x_train, feat_names = _encode(train, encoder, means, stds)
    assert_no_forbidden_features(feat_names)  # fail loud BEFORE any fit

    y_train = train[LABEL_COL].astype(int).to_numpy()

    # NO rebalancing (no class_weight).
    gbt = HistGradientBoostingClassifier(random_state=MODEL_SEED)
    gbt.fit(x_train, y_train)
    logit = LogisticRegression(max_iter=1000, random_state=MODEL_SEED)
    logit.fit(x_train, y_train)

    def _evaluate(part: pd.DataFrame) -> dict[str, Any]:
        X, _ = _encode(part, encoder, means, stds)
        y = part[LABEL_COL].astype(int).to_numpy()
        gbt_prob = clip01(gbt.predict_proba(X)[:, 1])
        logit_prob = clip01(logit.predict_proba(X)[:, 1])
        strata = {s: part[s].to_numpy() for s in STRATA}
        return {
            "gbt": classifier_panel(y, gbt_prob),
            "logit": classifier_panel(y, logit_prob),
            "per_stratum_gbt": per_stratum(strata, y, gbt_prob),
            "per_stratum_logit": per_stratum(strata, y, logit_prob),
            "_y": y,
            "_gbt_prob": gbt_prob,
            "_logit_prob": logit_prob,
        }

    test_eval = _evaluate(test)
    val_eval = _evaluate(val)

    plot_paths = {
        "pr_synth_structural_gbt": str(
            plot_pr_curve(test_eval["_y"], test_eval["_gbt_prob"], out_root / "pr_synth_struct_gbt.png")
        ),
        "calibration_synth_structural_gbt": str(
            plot_calibration_curve(
                test_eval["_y"], test_eval["_gbt_prob"], out_root / "calib_synth_struct_gbt.png"
            )
        ),
    }

    metrics: dict[str, Any] = {
        "data_source": "SYNTHETIC_T2D",
        "task": "1b_synthetic_structural_only_ablation",
        "model_class": "HistGradientBoostingClassifier + LogisticRegression",
        "features": {"numeric": STATIC_NUMERIC, "categorical": STATIC_CATEGORICAL},
        "feature_names": feat_names,
        "label": LABEL_COL,
        "censoring_note": (
            "No early right-censoring in this cohort: censored==observed completer==negative. "
            "Non-droppers are legitimate negatives, not excluded."
        ),
        "n_participants_per_fold": {
            "train": int(len(train)),
            "val": int(len(val)),
            "test": int(len(test)),
        },
        "test": {
            "gbt": test_eval["gbt"],
            "logit": test_eval["logit"],
            "per_stratum_gbt": test_eval["per_stratum_gbt"],
            "per_stratum_logit": test_eval["per_stratum_logit"],
        },
        "val": {"gbt": val_eval["gbt"], "logit": val_eval["logit"]},
        "artifacts": plot_paths,
    }
    (out_root / "baseline_synthetic_structural.json").write_text(
        json.dumps(metrics, indent=2), encoding="utf-8"
    )
    return metrics
