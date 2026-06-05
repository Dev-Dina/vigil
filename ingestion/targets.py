"""Compute the real AACT-derived calibration targets from the clean ``ref_*`` tables.

These are the statistics the synthetic cohort must reproduce (specs/data.md, six targets).
They are computed from outcomes and are used ONLY for calibration / reporting — never as
per-participant features (that would be target leakage).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class CalibrationTargets:
    """Real aggregate statistics the synthetic cohort is calibrated against."""

    overall_dropout_rate: float
    dropout_by_stratum: dict[str, float]  # stratum key -> dropout rate
    reason_mix: dict[str, float]  # reason -> share
    early_fraction: float  # fraction of dropout that is "early"
    covariate_signs: dict[str, int]  # covariate -> sign of association with dropout
    enrollment_by_stratum: dict[str, float]  # stratum -> mean enrollment
    arm_count_by_stratum: dict[str, float]  # stratum -> mean n_arms
    strata: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_dropout_rate": self.overall_dropout_rate,
            "dropout_by_stratum": self.dropout_by_stratum,
            "reason_mix": self.reason_mix,
            "early_fraction": self.early_fraction,
            "covariate_signs": self.covariate_signs,
            "enrollment_by_stratum": self.enrollment_by_stratum,
            "arm_count_by_stratum": self.arm_count_by_stratum,
        }


def stratum_key(phase: str, area: str, sponsor_class: str) -> str:
    return f"{phase}|{area}|{sponsor_class}"


def _weighted_dropout(arm: pd.DataFrame) -> float:
    started = arm["started"].sum()
    not_completed = arm["not_completed"].sum()
    return float(not_completed / started) if started > 0 else 0.0


def compute_targets(
    trials: pd.DataFrame,
    arms: pd.DataFrame,
    withdrawals: pd.DataFrame,
) -> CalibrationTargets:
    merged = arms.merge(
        trials[
            [
                "nct_id",
                "phase",
                "therapeutic_area",
                "sponsor_class",
                "masking",
                "n_sites",
                "planned_duration_days",
                "enrollment",
                "n_arms",
            ]
        ],
        on="nct_id",
        how="left",
    )

    overall = _weighted_dropout(merged)

    # Target 2: dropout by stratum (phase x area x sponsor_class).
    by_stratum: dict[str, float] = {}
    enroll_by_stratum: dict[str, float] = {}
    arms_by_stratum: dict[str, float] = {}
    grouped = merged.groupby(["phase", "therapeutic_area", "sponsor_class"])
    for (phase, area, sclass), g in grouped:
        key = stratum_key(str(phase), str(area), str(sclass))
        by_stratum[key] = _weighted_dropout(g)
        enroll_by_stratum[key] = float(
            trials[
                (trials["phase"] == phase)
                & (trials["therapeutic_area"] == area)
                & (trials["sponsor_class"] == sclass)
            ]["enrollment"].mean()
        )
        arms_by_stratum[key] = float(
            trials[
                (trials["phase"] == phase)
                & (trials["therapeutic_area"] == area)
                & (trials["sponsor_class"] == sclass)
            ]["n_arms"].mean()
        )

    # Target 3: reason mix over the controlled vocab.
    reason_totals = withdrawals.groupby("reason")["count"].sum()
    total = float(reason_totals.sum())
    reason_mix = (
        {str(k): float(v) / total for k, v in reason_totals.items()}
        if total > 0
        else {}
    )

    # Target 4: dropout timing early-vs-late. ASSUMPTION (not a fitted value): AACT
    # aggregates lack per-event timing, so 0.6 is an *assumed* share of dropout in the
    # first half of the planned duration, NOT estimated from the data. Labelled as an
    # assumption in the synthetic manifest and the calibration report.
    # TODO: revisit early_fraction if a real per-event timing source becomes available
    early_fraction = 0.6

    # Target 5: covariate -> dropout association signs (point-biserial style via grouping).
    covariate_signs = {
        "planned_duration_days": _sign_assoc(
            merged, "planned_duration_days", "dropout_rate"
        ),
        "n_sites": _sign_assoc(merged, "n_sites", "dropout_rate"),
    }

    return CalibrationTargets(
        overall_dropout_rate=overall,
        dropout_by_stratum=by_stratum,
        reason_mix=reason_mix,
        early_fraction=early_fraction,
        covariate_signs=covariate_signs,
        enrollment_by_stratum=enroll_by_stratum,
        arm_count_by_stratum=arms_by_stratum,
        strata=sorted(by_stratum.keys()),
    )


def _sign_assoc(df: pd.DataFrame, x: str, y: str) -> int:
    if df[x].nunique() < 2 or len(df) < 3:
        return 0
    corr = np.corrcoef(df[x].to_numpy(float), df[y].to_numpy(float))[0, 1]
    if np.isnan(corr):
        return 0
    return int(np.sign(corr))
