"""TASK 2 — pre-register the bar BEFORE the sequence model is fit.

Ordering is the point: this file is written from 1b's *actual* test PR-AUC, with a timestamp,
**before** any LSTM weight is touched. The result is only appended afterwards (by the pipeline).
The bar is stated as NUMBERS, justified from 1b's value.
"""

from __future__ import annotations

import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from models.config import MODELS_ROOT

T2D_ROOT = MODELS_ROOT / "t2d"

#: The sequence model must exceed 1b's PR-AUC by at least this absolute margin (test fold).
PR_AUC_MARGIN: float = 0.05

#: The sequence model must achieve at least this median lead-time (visits before the event).
MIN_MEDIAN_LEAD_TIME_VISITS: float = 2.0


def write_preregistration(
    baseline_1b_pr_auc: float,
    *,
    out_root: Path = T2D_ROOT,
) -> dict[str, Any]:
    """Write ``preregistration.json`` from 1b's PR-AUC. MUST run before the LSTM fit."""
    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    bar_pr_auc = round(baseline_1b_pr_auc + PR_AUC_MARGIN, 4)
    prereg: dict[str, Any] = {
        "preregistered_at": datetime.now(timezone.utc).isoformat(),
        "written_before_sequence_fit": True,
        "baseline_1b_test_pr_auc": round(float(baseline_1b_pr_auc), 4),
        "criteria": {
            "pr_auc": {
                "rule": (
                    "sequence-model TEST PR-AUC must exceed the 1b synthetic-structural TEST "
                    f"PR-AUC by >= +{PR_AUC_MARGIN}"
                ),
                "baseline": round(float(baseline_1b_pr_auc), 4),
                "margin": PR_AUC_MARGIN,
                "must_reach": bar_pr_auc,
            },
            "lead_time": {
                "rule": (
                    "median lead-time (visits between first confident flag and the dropout "
                    f"event, on TEST droppers) must be >= {MIN_MEDIAN_LEAD_TIME_VISITS} visits"
                ),
                "must_reach": MIN_MEDIAN_LEAD_TIME_VISITS,
            },
        },
        "justification": (
            "The trajectory->dropout relationship is a planted, literature-shaped assumption "
            "(>=3 consecutive missed visits / hazard threshold, noisy, non-separable AUC~0.77). "
            "+0.05 PR-AUC is a material gain over a covariate-only floor without claiming "
            "separability the generator never planted; >=2 visits of lead-time is the minimum "
            "operationally useful early-warning window."
        ),
    }
    (out_root / "preregistration.json").write_text(
        json.dumps(prereg, indent=2), encoding="utf-8"
    )
    return prereg
