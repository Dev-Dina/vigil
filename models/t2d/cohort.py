"""The T2D modelling slice: predicate filter + the SINGLE shared temporal split.

The 755 T2D trials / 2,402 usable arms are reproduced *by predicate* (no new persisted state),
so the real baselines (1a) and the synthetic layers (1b, 3) all partition the exact same trial
set. Counts are asserted LOUD against the known anchors (755 / 2,402) so silent drift is caught.

Predicate (specs/data.md + task contract):
- clean ``ref_trial``: modelling-cohort phase AND ``sponsor_class == INDUSTRY``;
- raw ``browse_conditions``: ``mesh_term ILIKE '%Diabetes Mellitus, Type 2%'``;
- raw ``interventions``: ``intervention_type in {DRUG, BIOLOGICAL}``;
- clean ``ref_arm`` usable: ``started > 0 AND completed not null``.

The marginal/training guard (task contract): exclude arms with ``started < 20`` OR
``dropout_rate == 1.0`` (degenerate arms that trivially encode the outcome).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import pandas as pd

from ingestion.config import CLEAN_ROOT, RAW_ROOT
from models.cohort import modelling_cohort
from models.splits import Split, temporal_group_split

#: Known structural anchors — fail loud if the predicate drifts off these.
EXPECTED_T2D_TRIALS = 755
EXPECTED_T2D_USABLE_ARMS = 2402

#: Training/marginal guard thresholds (task contract).
MIN_STARTED = 20
DEGENERATE_DROPOUT_RATE = 1.0

#: Pinned raw AACT snapshot used for the mesh/intervention predicates.
RAW_SNAPSHOT = "2026-06-05"

_T2D_MESH = "Diabetes Mellitus, Type 2"
_DRUG_TYPES = frozenset({"DRUG", "BIOLOGICAL"})


@dataclass(frozen=True)
class T2DCohort:
    """The reproduced T2D slice: cohort trials, usable+guarded real arms, and the shared split."""

    trials: pd.DataFrame  # modelling-cohort T2D INDUSTRY trials (one row per nct_id)
    arms: pd.DataFrame  # usable real arms AFTER the started<20 / dropout==1 guard
    split: Split  # the ONE temporal group split shared by 1a / 1b / 3

    @property
    def nct_ids(self) -> frozenset[str]:
        return frozenset(self.trials["nct_id"])


def _t2d_nct_ids(raw_root: Path) -> frozenset[str]:
    """NCT ids that are T2D (mesh) AND drug/biological (intervention), from the raw snapshot."""
    snap = raw_root / RAW_SNAPSHOT
    bc = pd.read_json(snap / "browse_conditions.ndjson", lines=True)
    mesh_hits = bc["mesh_term"].str.contains(_T2D_MESH, case=False, na=False)
    t2d = frozenset(bc.loc[mesh_hits, "nct_id"])
    iv = pd.read_json(snap / "interventions.ndjson", lines=True)
    drug = frozenset(iv.loc[iv["intervention_type"].isin(_DRUG_TYPES), "nct_id"])
    return t2d & drug


def t2d_cohort_trials(
    ref_trial: pd.DataFrame, raw_root: Path = RAW_ROOT
) -> pd.DataFrame:
    """Reproduce the 755 T2D modelling-cohort INDUSTRY trials; fail loud on drift."""
    cohort = modelling_cohort(ref_trial)
    industry = cohort[cohort["sponsor_class"] == "INDUSTRY"]
    nct = _t2d_nct_ids(raw_root)
    trials = (
        industry[industry["nct_id"].isin(nct)]
        .drop_duplicates("nct_id")
        .reset_index(drop=True)
    )
    n = trials["nct_id"].nunique()
    if n != EXPECTED_T2D_TRIALS:
        raise ValueError(
            f"T2D cohort drift: expected {EXPECTED_T2D_TRIALS} trials, got {n}. "
            f"The predicate or the snapshot changed — STOP and reconcile."
        )
    return trials


def usable_t2d_arms(ref_arm: pd.DataFrame, trials: pd.DataFrame) -> pd.DataFrame:
    """Usable real arms (started>0, completed not null) within the T2D trials; assert 2,402."""
    nct = frozenset(trials["nct_id"])
    arms = ref_arm[ref_arm["nct_id"].isin(nct)]
    usable = arms[(arms["started"] > 0) & (arms["completed"].notna())].reset_index(
        drop=True
    )
    n = len(usable)
    if n != EXPECTED_T2D_USABLE_ARMS:
        raise ValueError(
            f"T2D usable-arm drift: expected {EXPECTED_T2D_USABLE_ARMS}, got {n}. "
            f"STOP and reconcile."
        )
    return usable


def apply_training_guard(arms: pd.DataFrame) -> pd.DataFrame:
    """Drop degenerate arms (started < 20 OR dropout_rate == 1.0) from the training marginal."""
    keep = (arms["started"] >= MIN_STARTED) & (
        arms["dropout_rate"] < DEGENERATE_DROPOUT_RATE
    )
    guarded = arms[keep].reset_index(drop=True)
    if guarded.empty:
        raise ValueError(
            "training guard removed every arm — refusing to train on an empty set"
        )
    return guarded


def build_t2d_cohort(
    ref_trial: pd.DataFrame,
    ref_arm: pd.DataFrame,
    raw_root: Path = RAW_ROOT,
) -> T2DCohort:
    """Reproduce the T2D slice and build the ONE temporal group split shared by 1a / 1b / 3."""
    trials = t2d_cohort_trials(ref_trial, raw_root)
    usable = usable_t2d_arms(ref_arm, trials)
    guarded = apply_training_guard(usable)
    split = temporal_group_split(trials)
    return T2DCohort(trials=trials, arms=guarded, split=split)


def load_t2d_cohort(
    clean_root: Path = CLEAN_ROOT, raw_root: Path = RAW_ROOT
) -> T2DCohort:
    """Load clean ``ref_*`` parquet + the raw snapshot and reproduce the T2D slice."""
    ref_trial = pd.read_parquet(clean_root / "ref_trial.parquet")
    ref_arm = pd.read_parquet(clean_root / "ref_arm.parquet")
    return build_t2d_cohort(ref_trial, ref_arm, raw_root)
