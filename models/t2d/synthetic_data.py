"""Loading + leakage-safe column governance for the synthetic T2D cohort.

ONE place that decides which synthetic columns may become features. The latent generator hazard
``miss_probability``, the ``synthetic`` flag, the ``*_baseline_imputed`` provenance flags, and
every outcome column (``dropped``, ``censored``, ``arm_real_dropout_rate``, ``dropout_visit_index``,
``dropout_reason``) are FORBIDDEN as features and are asserted-out before any fit. Fail loud.

Censoring semantics in this cohort (verified, carded loudly): every participant is observed to a
TERMINAL state — a dropper's engagement sequence is truncated at ``dropout_visit_index`` (their
last observed visit), a non-dropper (``censored == True``) is observed through the full planned
``n_visits`` (completed / rolled over, did NOT drop). There is **no early right-censoring**: no
participant's observation ends before a *knowable* outcome, so ``censored`` here is a legitimate
observed NEGATIVE, not an unknown label. The sequence model still enforces per-visit right-
censoring at decision time t (a participant whose observation has ended by t contributes no label
for t).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from ingestion.config import SYNTHETIC_ROOT
from ingestion.errors import LeakageError

T2D_SYNTH_ROOT = SYNTHETIC_ROOT / "t2d"

#: Static baseline covariates the structural baseline (1b) and the sequence static context use.
#: Observable at enrollment; NO trajectory, NO outcome, NO latent hazard, NO provenance.
STATIC_NUMERIC: list[str] = [
    "age_years",
    "hba1c_pct",
    "bmi",
    "n_sites",
    "planned_duration_days",
]
STATIC_CATEGORICAL: list[str] = ["sex", "phase", "arm_type"]

#: Per-visit observable trajectory features the sequence model consumes (NEVER miss_probability).
SEQ_NUMERIC: list[str] = [
    "attended",  # bool -> float
    "missed",  # bool -> float
    "cumulative_missed",
    "consecutive_missed",
    "visit_index",
]

#: Columns that must NEVER be a feature anywhere (latent hazard / provenance / outcome / id).
FORBIDDEN_FEATURES: frozenset[str] = frozenset(
    {
        "miss_probability",  # the generator's LATENT hazard — using it recovers the planted rule
        "synthetic",
        "age_baseline_imputed",
        "hba1c_baseline_imputed",
        "bmi_baseline_imputed",
        "dropped",
        "censored",
        "arm_real_dropout_rate",
        "dropout_visit_index",
        "dropout_reason",
        "participant_id",
        "nct_id",
        "arm_id",
        "arm_band",
    }
)

#: Strata for per-stratum reporting (task contract).
STRATA: tuple[str, ...] = ("arm_type", "phase")

#: The terminal participant-level label column.
LABEL_COL = "dropped"


@dataclass(frozen=True)
class SyntheticT2D:
    """The synthetic T2D cohort frames (already labelled ``synthetic=True``)."""

    participants: pd.DataFrame
    engagement: pd.DataFrame


def assert_no_forbidden_features(feature_names: list[str]) -> None:
    """Fail loud if any forbidden (latent/provenance/outcome/id) column is used as a feature."""
    leaked = [n for n in feature_names if n.split("=")[0] in FORBIDDEN_FEATURES]
    # also catch one-hot expansions like ``sex=MALE`` whose base is allowed, but the raw
    # forbidden names themselves (exact match) must never appear.
    leaked += [n for n in feature_names if n in FORBIDDEN_FEATURES]
    leaked = sorted(set(leaked))
    if leaked:
        raise LeakageError(
            f"forbidden synthetic column(s) used as features: {leaked} "
            f"(miss_probability is the LATENT hazard; provenance/outcome/id are never features)"
        )


def load_synthetic_t2d(root: Path = T2D_SYNTH_ROOT) -> SyntheticT2D:
    """Read the synthetic participants + engagement parquet."""
    participants = pd.read_parquet(root / "participants.parquet")
    engagement = pd.read_parquet(root / "engagement.parquet")
    return SyntheticT2D(participants=participants, engagement=engagement)
