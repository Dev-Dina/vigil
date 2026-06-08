"""Phase 3 build-time model configuration.

Build-time only: artifacts live under ``data/models/`` (git-ignored) and are never reached at
runtime or by an agent. The modelling cohort, horizon, seed, and split fractions are pinned
here once and shared by the cohort/split/feature layers so the three never drift.
"""

from __future__ import annotations

from ingestion import SYNTHETIC_SEED
from ingestion.config import DATA_ROOT

#: Phases with enough participant-flow volume / comparable dropout dynamics to model
#: (specs/data.md "Modelling cohort"). All other phases are reference-only.
MODELLING_PHASES: frozenset[str] = frozenset(
    {"PHASE1/PHASE2", "PHASE2", "PHASE2/PHASE3", "PHASE3"}
)

#: Dropout label horizon H in days (specs/data.md "Prediction task").
HORIZON_DAYS: int = 28

#: Single fixed seed, reused from ingestion (one seed for the whole build).
MODEL_SEED: int = SYNTHETIC_SEED

#: Git-ignored artifacts root (model cards, plots, metrics).
MODELS_ROOT = DATA_ROOT / "models"

#: Temporal split fractions by trial count (train is the remainder). Stated once.
VAL_FRACTION: float = 0.2
TEST_FRACTION: float = 0.2
TRAIN_FRACTION: float = 1.0 - VAL_FRACTION - TEST_FRACTION

#: The sparse ``sponsor_class`` folded into ``ACADEMIC_OTHER`` for feature encoding
#: (specs/data.md "Feature contract": OTHER_GOV folded/regularized). Reporting still groups
#: by the original ``sponsor_class``.
SPONSOR_CLASS_FOLD: dict[str, str] = {"OTHER_GOV": "ACADEMIC_OTHER"}
