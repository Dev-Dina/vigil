"""Fail-loud leakage checks for the modelling matrices (specs/data.md "Leakage rules").

Mirrors the ingestion leakage idiom (``ingestion/features.py``): no feature derived from the
outcome; group-disjoint splits; and — for the later sequence layer — every feature timestamp
strictly before the decision time ``t``. Everything raises :class:`ingestion.errors.LeakageError`.
"""

from __future__ import annotations

import pandas as pd

from ingestion.errors import LeakageError
from ingestion.leakage import assert_group_disjoint as _assert_group_disjoint
from models.features.contract import FeatureMatrix

#: Base tokens that must never appear as a feature (outcome / identity / synthetic).
# miss_probability: the synthetic generator's latent hazard — using it would trivially
# recover the planted signal (specs/scoring.md § Leakage-test invariants, inv 4).
# time_to_event / event_observed: TTE outcome columns shared across survival + sequence.
# arm_real_dropout_rate: realized outcome from AACT; pure leakage if it reached a feature.
_FORBIDDEN_TOKENS: frozenset[str] = frozenset(
    {
        "dropout_rate",
        "not_completed",
        "completed",
        "dropped",
        "time_to_event_days",
        "time_to_event",
        "event_observed",
        "censored",
        "synthetic",
        "nct_id",
        "arm_id",
        "study_url",
        "miss_probability",
        "arm_real_dropout_rate",
    }
)


def _base_token(feature_name: str) -> str:
    """Strip one-hot suffix (``phase_PHASE2`` -> ``phase``) and the ``_missing`` indicator."""
    base = feature_name.split("=")[0]
    # OneHotEncoder uses ``<col>_<level>``; the contract's columns have no underscores except
    # the level join, so take the known feature prefix conservatively.
    for known in ("_missing",):
        if base.endswith(known):
            base = base[: -len(known)]
    return base


def assert_no_outcome_features(feature_names: list[str]) -> None:
    """Raise if any feature is outcome-, identity-, or synthetic-derived."""
    leaked = []
    for name in feature_names:
        token = name
        # exact forbidden, or a dropout/outcome substring, or the bare base before a one-hot join
        low = name.lower()
        if (
            token in _FORBIDDEN_TOKENS
            or "dropout" in low
            or low in _FORBIDDEN_TOKENS
            or any(
                low == f or low.startswith(f + "_")
                for f in ("nct_id", "arm_id", "study_url")
            )
        ):
            leaked.append(name)
    if leaked:
        raise LeakageError(
            f"outcome/identity/synthetic column(s) used as features: {leaked}"
        )


def assert_group_disjoint(matrices: dict[str, FeatureMatrix]) -> None:
    """Raise if any ``nct_id`` appears in more than one fold (shared ingestion primitive)."""
    _assert_group_disjoint({fold: m.nct_id for fold, m in matrices.items()})


def assert_feature_time_before_t(
    df: pd.DataFrame, *, t_col: str, feature_time_col: str
) -> None:
    """Sequence-layer guard: every feature observation strictly precedes decision time t."""
    if t_col not in df.columns or feature_time_col not in df.columns:
        raise ValueError(f"need columns '{t_col}' and '{feature_time_col}'")
    bad = df[df[feature_time_col] >= df[t_col]]
    if len(bad):
        raise LeakageError(
            f"{len(bad)} row(s) have a feature timestamp >= decision time t "
            f"(future leakage)."
        )


def run_smoke(matrices: dict[str, FeatureMatrix]) -> None:
    """Run the non-sequence leakage invariants over the built matrices."""
    for fold, m in matrices.items():
        assert_no_outcome_features(m.feature_names)
        if m.fold != fold:
            raise LeakageError(f"matrix fold label mismatch: {m.fold} != {fold}")
    assert_group_disjoint(matrices)
