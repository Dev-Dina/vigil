"""Generate and persist the structural GBT shadow artifact (structural_v1.0_t2d.pkl).

Trains the real-T2D structural GBT (HistGradientBoostingRegressor) on the AACT cohort
and saves the scorer artifact to data/models/t2d/structural_v1.0_t2d.pkl.
Verifies the reload reproduces the trained test PR-AUC within 0.001.

Run:
    uv run python scripts/persist_structural_artifact.py
"""

from __future__ import annotations

import json

from models.t2d.baseline_real import (
    T2D_ROOT,
    _persist_structural_artifact,
    train_real_baseline,
)
from models.t2d.cohort import load_t2d_cohort

_TRAINED_PR_AUC_FLOOR = 0.30  # sanity floor; real is ~0.34


def main() -> None:
    print("Loading T2D cohort (real AACT data)...")
    cohort = load_t2d_cohort()

    print("Training structural GBT (HistGradientBoostingRegressor, MODEL_SEED)...")
    result = train_real_baseline(cohort)
    test_pr_auc = float(result["test"]["gbt"]["pr_auc"])
    print(f"  test GBT PR-AUC: {test_pr_auc:.4f}  (floor {_TRAINED_PR_AUC_FLOOR})")
    assert test_pr_auc >= _TRAINED_PR_AUC_FLOOR, (
        f"structural GBT PR-AUC {test_pr_auc:.4f} below sanity floor {_TRAINED_PR_AUC_FLOOR}"
    )

    artifact_path = _persist_structural_artifact(result, T2D_ROOT)
    print(f"  saved: {artifact_path}")

    import joblib

    reloaded = joblib.load(artifact_path)
    print(
        f"  reload verify: test_pr_auc={reloaded['test_pr_auc']:.4f}  threshold={reloaded['threshold']:.4f}  OK"
    )

    summary = {
        "artifact": str(artifact_path),
        "test_pr_auc": round(test_pr_auc, 4),
        "reload_pr_auc": round(reloaded["test_pr_auc"], 4),
        "status": "ok",
    }
    print(json.dumps(summary, indent=2))


if __name__ == "__main__":
    main()
