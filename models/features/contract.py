"""The feature contract (specs/data.md "Feature contract"), as fit-on-train code.

The modelling row unit for the real-registry baselines is the **arm** (target =
``ref_arm.dropout_rate``). This module:

- assembles arm rows joined to their (modelling-cohort) trial context;
- one-hot encodes categoricals fit on **train only** (unseen levels -> all-zero), with the
  sparse ``OTHER_GOV`` ``sponsor_class`` **folded into ``ACADEMIC_OTHER``** (recorded);
- standardizes numerics fit on **train only**, **never imputing** — a NaN stays NaN and an
  explicit ``<col>_missing`` boolean is emitted (so ``max_age_years_missing`` carries the
  missingness signal);
- keeps the original ``sponsor_class`` only for per-class *reporting* (never as an encoded
  identity), and drops every outcome/identity/synthetic column from the matrix.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import pandas as pd
from sklearn.preprocessing import OneHotEncoder

from models.config import SPONSOR_CLASS_FOLD

# --- feature spec (explicit; specs/data.md "Features" + "Feature contract") ----------------
CATEGORICAL_FEATURES: list[str] = [
    "phase",
    "therapeutic_area",
    "sponsor_class",  # folded (OTHER_GOV -> ACADEMIC_OTHER) before encoding
    "allocation",
    "intervention_model",
    "masking",
    "primary_purpose",
    "gender",
    "arm_type",
]
NUMERIC_FEATURES: list[str] = [
    "enrollment",
    "n_arms",
    "n_sites",
    "n_countries",
    "planned_duration_days",
    "min_age_years",
    "max_age_years",
    "started",  # arm size (denominator) — known pre-outcome
]
BOOLEAN_FEATURES: list[str] = ["healthy_volunteers"]

# Carried for joins/targets/grouping but NEVER fed to the matrix as features.
_KEYS = ["nct_id", "arm_id", "start_date"]
_TARGET = "dropout_rate"
_GROUP = "sponsor_class"  # original (unfolded) — for per-sponsor_class reporting only
# Outcome/realized/identity columns explicitly excluded from features.
EXCLUDED_FROM_FEATURES: frozenset[str] = frozenset(
    {
        "dropout_rate",
        "not_completed",
        "completed",
        "actual_duration_days",  # realized post-hoc -> leakage
        "results_reported",
        "study_type",
        "study_url",
        "synthetic",
    }
)


def assemble_rows(cohort_trials: pd.DataFrame, arms: pd.DataFrame) -> pd.DataFrame:
    """One row per arm: keys + target + grouping + the contracted feature source columns."""
    needed_trial = set(
        _KEYS[:1]
        + ["start_date", _GROUP]
        + CATEGORICAL_FEATURES
        + NUMERIC_FEATURES
        + BOOLEAN_FEATURES
    )
    trial_cols = [c for c in cohort_trials.columns if c in needed_trial]
    rows = arms.merge(cohort_trials[trial_cols], on="nct_id", how="inner")
    if rows.empty:
        raise ValueError(
            "assemble_rows produced no rows (arms did not join to cohort trials)"
        )
    keep = list(
        dict.fromkeys(
            _KEYS
            + [_TARGET, _GROUP]
            + CATEGORICAL_FEATURES
            + NUMERIC_FEATURES
            + BOOLEAN_FEATURES
        )
    )
    missing = [c for c in keep if c not in rows.columns]
    if missing:
        raise ValueError(f"assembled rows missing required columns {missing}")
    return rows[keep].reset_index(drop=True)


@dataclass
class FeatureMatrix:
    """A transformed fold: design matrix ``X`` + target ``y`` + keys for grouping/leakage."""

    X: pd.DataFrame
    y: pd.Series
    feature_names: list[str]
    nct_id: pd.Series
    arm_id: pd.Series
    sponsor_class: pd.Series  # original, for per-class reporting
    fold: str


@dataclass
class ContractTransformer:
    """Fit-on-train categorical one-hot + NaN-aware numeric standardizer + bool passthrough."""

    encoder_: OneHotEncoder | None = None
    cat_names_: list[str] = field(default_factory=list)
    means_: dict[str, float] = field(default_factory=dict)
    stds_: dict[str, float] = field(default_factory=dict)
    fold_record_: dict[str, int] = field(default_factory=dict)
    fitted_on_: str | None = None

    # --- sponsor_class fold (recorded, never silent) ---------------------------------------
    def _fold_categoricals(self, frame: pd.DataFrame) -> pd.DataFrame:
        sub = frame[CATEGORICAL_FEATURES].astype("string").fillna("__NA__")
        n_folded = int((sub["sponsor_class"] == "OTHER_GOV").sum())
        if n_folded:
            key = "sponsor_class:OTHER_GOV->ACADEMIC_OTHER"
            self.fold_record_[key] = self.fold_record_.get(key, 0) + n_folded
        sub["sponsor_class"] = sub["sponsor_class"].replace(SPONSOR_CLASS_FOLD)
        return sub

    def fit(
        self, train_rows: pd.DataFrame, *, fold_name: str = "train"
    ) -> ContractTransformer:
        folded = self._fold_categoricals(train_rows)
        self.encoder_ = OneHotEncoder(handle_unknown="ignore", sparse_output=False)
        self.encoder_.fit(folded)
        self.cat_names_ = list(
            self.encoder_.get_feature_names_out(CATEGORICAL_FEATURES)
        )
        for col in NUMERIC_FEATURES:
            vals = pd.to_numeric(train_rows[col], errors="coerce").to_numpy(dtype=float)
            finite = vals[np.isfinite(vals)]
            self.means_[col] = float(finite.mean()) if finite.size else 0.0
            std = float(finite.std(ddof=0)) if finite.size else 1.0
            self.stds_[col] = std if std > 0.0 else 1.0
        self.fitted_on_ = fold_name
        return self

    def transform(self, rows: pd.DataFrame) -> tuple[pd.DataFrame, list[str]]:
        if self.encoder_ is None or self.fitted_on_ is None:
            raise RuntimeError("ContractTransformer.transform called before fit")
        index = rows.index
        # categoricals
        cat = pd.DataFrame(
            self.encoder_.transform(self._fold_categoricals(rows)),
            columns=self.cat_names_,
            index=index,
        )
        # numerics — standardize with train stats; NaN stays NaN (never imputed); + indicator
        num_cols: dict[str, np.ndarray] = {}
        for col in NUMERIC_FEATURES:
            raw = pd.to_numeric(rows[col], errors="coerce").to_numpy(dtype=float)
            missing = ~np.isfinite(raw)
            scaled = (raw - self.means_[col]) / self.stds_[col]
            scaled[missing] = np.nan
            num_cols[col] = scaled
            num_cols[f"{col}_missing"] = missing.astype(float)
        num = pd.DataFrame(num_cols, index=index)
        # booleans
        boo = pd.DataFrame(
            {c: rows[c].astype("boolean").astype("float64") for c in BOOLEAN_FEATURES},
            index=index,
        )
        X = pd.concat([cat, num, boo], axis=1)
        return X, list(X.columns)

    def describe(self) -> dict[str, object]:
        return {
            "fitted_on": self.fitted_on_,
            "n_categorical_features": len(self.cat_names_),
            "sponsor_class_fold": dict(self.fold_record_),
        }


def build_matrices(
    rows: pd.DataFrame, split, *, transformer: ContractTransformer | None = None
) -> dict[str, FeatureMatrix]:
    """Assign folds from ``split``, fit the transformer on TRAIN only, transform each fold."""
    from models.splits import assign_folds  # local import to avoid a cycle

    framed = assign_folds(rows, split)
    transformer = transformer or ContractTransformer()
    train_rows = framed[framed["fold"] == "train"]
    transformer.fit(train_rows, fold_name="train")

    out: dict[str, FeatureMatrix] = {}
    for fold in ("train", "val", "test"):
        part = framed[framed["fold"] == fold]
        X, names = transformer.transform(part)
        out[fold] = FeatureMatrix(
            X=X,
            y=part[_TARGET].reset_index(drop=True),
            feature_names=names,
            nct_id=part["nct_id"].reset_index(drop=True),
            arm_id=part["arm_id"].reset_index(drop=True),
            sponsor_class=part[_GROUP].reset_index(drop=True),
            fold=fold,
        )
    return out
