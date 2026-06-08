"""Build-time model utilities (Phase 3). Never reached at runtime or by an agent.

``models.baselines`` (which pulls in shap + matplotlib) is intentionally NOT re-exported here,
so importing the lightweight harness (cohort / splits / features / leakage) stays cheap. Use
``from models.baselines import train_baselines`` when you need the trainer.
"""

from models.cohort import cohort_nct_ids, load_modelling_cohort, modelling_cohort
from models.config import (
    HORIZON_DAYS,
    MODEL_SEED,
    MODELLING_PHASES,
    MODELS_ROOT,
    SPONSOR_CLASS_FOLD,
    TEST_FRACTION,
    VAL_FRACTION,
)
from models.features.contract import (
    ContractTransformer,
    FeatureMatrix,
    assemble_rows,
    build_matrices,
)
from models.leakage_check import (
    assert_feature_time_before_t,
    assert_group_disjoint,
    assert_no_outcome_features,
    run_smoke,
)
from models.splits import Split, assign_folds, temporal_group_split

__all__ = [
    "HORIZON_DAYS",
    "MODEL_SEED",
    "MODELLING_PHASES",
    "MODELS_ROOT",
    "SPONSOR_CLASS_FOLD",
    "TEST_FRACTION",
    "VAL_FRACTION",
    "ContractTransformer",
    "FeatureMatrix",
    "Split",
    "assemble_rows",
    "assign_folds",
    "assert_feature_time_before_t",
    "assert_group_disjoint",
    "assert_no_outcome_features",
    "build_matrices",
    "cohort_nct_ids",
    "load_modelling_cohort",
    "modelling_cohort",
    "run_smoke",
    "temporal_group_split",
]
