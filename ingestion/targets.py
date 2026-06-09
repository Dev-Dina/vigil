"""The real AACT-derived calibration targets the synthetic cohort must reproduce.

specs/data.md ("Synthetic cohort" + "Calibration targets") names six targets. The REAL
values come from the captured EDA over the validated ``ref_*`` tables
(``data/eda/eda_summary.json``) — they are NOT re-extracted here; the EDA summary (with its
AACT snapshot date) is the single provenance-stamped source. ``compute_targets`` loads those
real values and attaches the composite-stratum scaffolding the generator samples over.

These statistics are computed from outcomes and are used ONLY for calibration / reporting —
never as per-participant features (that would be target leakage, specs/data.md leakage rules).
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import pandas as pd

from ingestion.config import DATA_ROOT

# Provenance: the captured real EDA summary over the validated ref_* tables. Its embedded
# AACT snapshot date is the provenance key recorded in the synthetic manifest.
EDA_SUMMARY_PATH = DATA_ROOT / "eda" / "eda_summary.json"

# Enrollment-size bands (must match ingestion.eda.report._enrollment_bucket so the real EDA
# marginals line up with the synthetic cohort's bands).
ENROLLMENT_BANDS: tuple[tuple[str, float, float], ...] = (
    ("1-50", 0.0, 50.0),
    ("51-100", 50.0, 100.0),
    ("101-250", 100.0, 250.0),
    ("251-500", 250.0, 500.0),
    ("501-1000", 500.0, 1000.0),
    ("1000+", 1000.0, float("inf")),
)


@dataclass(frozen=True)
class CalibrationTargets:
    """Real aggregate statistics the synthetic cohort is calibrated against.

    Marginal dropout dicts (``*_by_*``) are the REAL EDA values; ``dropout_by_stratum`` is the
    composite (phase x area x sponsor_class) scaffold the generator samples over, recomputed
    from the frames it generates from so per-trial sampling stays self-consistent.
    """

    overall_dropout_rate: (
        float  # real TRIAL-MEAN overall — the calibrated quantity (target 1)
    )
    overall_dropout_rate_participant_weighted: (
        float  # real pw overall (recorded alongside)
    )
    dropout_by_phase: dict[str, float]
    dropout_by_therapeutic_area: dict[str, float]
    dropout_by_sponsor_class: dict[str, float]
    dropout_by_blinding: dict[str, float]  # {"BLINDED": .., "OPEN_LABEL": ..}
    dropout_by_site_count: dict[str, float]  # {"SINGLE_SITE": .., "MULTI_SITE": ..}
    dropout_by_enrollment_band: dict[str, float]
    reason_mix: dict[str, float]  # ratified vocab; censoring excluded (specs/data.md)
    early_fraction: float  # ASSUMPTION (not fitted) — see note below / targets.py:113
    covariate_signs: dict[str, int]  # covariate -> sign of association with dropout
    enrollment_by_stratum: dict[str, float]  # composite stratum -> mean enrollment
    arm_count_by_stratum: dict[str, float]  # composite stratum -> mean n_arms
    dropout_by_stratum: dict[
        str, float
    ]  # composite stratum -> dropout (generator scaffold)
    provenance: dict[str, Any] = field(default_factory=dict)
    strata: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "overall_dropout_rate": self.overall_dropout_rate,
            "overall_dropout_rate_kind": "real trial-mean (the calibrated quantity)",
            "overall_dropout_rate_participant_weighted": (
                self.overall_dropout_rate_participant_weighted
            ),
            "dropout_by_phase": self.dropout_by_phase,
            "dropout_by_therapeutic_area": self.dropout_by_therapeutic_area,
            "dropout_by_sponsor_class": self.dropout_by_sponsor_class,
            "dropout_by_blinding": self.dropout_by_blinding,
            "dropout_by_site_count": self.dropout_by_site_count,
            "dropout_by_enrollment_band": self.dropout_by_enrollment_band,
            "dropout_by_stratum": self.dropout_by_stratum,
            "reason_mix": self.reason_mix,
            "early_fraction": self.early_fraction,
            "early_fraction_kind": "ASSUMPTION (not fitted; AACT lacks per-event timing)",
            "covariate_signs": self.covariate_signs,
            "enrollment_by_stratum": self.enrollment_by_stratum,
            "arm_count_by_stratum": self.arm_count_by_stratum,
            "provenance": self.provenance,
        }


def stratum_key(phase: str, area: str, sponsor_class: str) -> str:
    return f"{phase}|{area}|{sponsor_class}"


def _weighted_dropout(arm: pd.DataFrame) -> float:
    started = arm["started"].sum()
    not_completed = arm["not_completed"].sum()
    return float(not_completed / started) if started > 0 else 0.0


def _load_eda_summary(path: Path) -> dict[str, Any]:
    """Load the captured real EDA summary. Fail LOUD if missing or malformed.

    The EDA summary is the provenance-stamped source for the real calibration targets;
    it is never silently defaulted (specs/data.md: validation fails loud).
    """
    if not path.exists():
        raise FileNotFoundError(
            f"Real EDA summary not found at {path}. Calibration targets are sourced from the "
            f"captured EDA over the validated ref_* tables; run `uv run python -m ingestion.eda` "
            f"first. Targets are never silently defaulted."
        )
    summary = json.loads(path.read_text(encoding="utf-8"))
    required = {
        "overall_dropout_rate",
        "dropout_by_phase",
        "dropout_by_therapeutic_area",
        "dropout_by_sponsor_class",
        "dropout_by_site_count",
        "dropout_by_enrollment_size",
        "withdrawal_reason_mix",
        "covariate_dropout_associations",
    }
    missing = sorted(required - summary.keys())
    if missing:
        raise KeyError(
            f"EDA summary at {path} is missing required keys: {missing}. It cannot be used as a "
            f"calibration source. (Re-run the EDA stage.)"
        )
    return summary


def _mean_dropout_map(node: dict[str, Any]) -> dict[str, float]:
    """Pull {label -> mean_dropout} from an EDA ``dropout_by_*`` block, dropping NA bands."""
    return {
        str(k): float(v["mean_dropout"])
        for k, v in node.items()
        if v.get("n_trials", 0) > 0
    }


def _covariate_signs_from_eda(assocs: list[dict[str, Any]]) -> dict[str, int]:
    """Real covariate->dropout association SIGNS (specs/data.md target 5).

    From the EDA: n_sites / n_countries / planned_duration rise with dropout (+1); enrollment
    and n_arms are near-zero (0, treated as "no required sign"); blinded < open-label (the
    blinding sign is encoded as the open-vs-blinded gap, +1 = open-label drops more).
    """
    signs: dict[str, int] = {}
    for a in assocs:
        cov = a.get("covariate")
        if cov == "blinded(vs open label)":
            gap = float(a["open_mean_dropout"]) - float(a["blinded_mean_dropout"])
            # +1 == open-label dropout exceeds blinded (the real direction).
            signs["open_label_vs_blinded"] = int((gap > 0) - (gap < 0))
            continue
        r = float(a.get("pearson_r", 0.0))
        # Near-zero associations (|r| < 0.01) carry no required sign: enrollment, n_arms.
        signs[str(cov)] = 0 if abs(r) < 0.01 else int((r > 0) - (r < 0))
    return signs


def compute_targets(
    trials: pd.DataFrame,
    arms: pd.DataFrame,
    withdrawals: pd.DataFrame,
    *,
    eda_path: Path = EDA_SUMMARY_PATH,
) -> CalibrationTargets:
    """Assemble the calibration targets.

    Real target VALUES (overall, marginals, reason-mix, covariate signs) come from the captured
    EDA summary (provenance-stamped, not re-extracted). The composite-stratum scaffolding the
    generator samples over (``dropout_by_stratum``, ``enrollment_by_stratum``,
    ``arm_count_by_stratum``) is recomputed from the passed frames so per-trial sampling is
    self-consistent with the cohort being generated.
    """
    eda = _load_eda_summary(eda_path)

    overall_node = eda["overall_dropout_rate"]
    # The synthetic cohort gives every real trial an equal number of participants, so its
    # per-stratum and overall dropout are TRIAL-MEANS. Calibration therefore grades against the
    # real TRIAL-MEAN family (overall 0.2018 and the trial-mean ``dropout_by_*`` marginals),
    # which are mutually consistent. The participant-weighted overall (0.151) is recorded
    # alongside (both real values are kept, per the task) but is a different aggregation.
    overall = float(overall_node["trial_mean"])
    overall_participant_weighted = float(overall_node["participant_weighted"])

    dropout_by_phase = _mean_dropout_map(eda["dropout_by_phase"])
    dropout_by_area = _mean_dropout_map(eda["dropout_by_therapeutic_area"])
    dropout_by_band = _mean_dropout_map(eda["dropout_by_enrollment_size"])
    dropout_by_site_count = _mean_dropout_map(eda["dropout_by_site_count"])
    dropout_by_sponsor_class = _mean_dropout_map(eda["dropout_by_sponsor_class"])

    # Blinding marginal: EDA covariate block carries the (trial-mean) blinded vs open means.
    blind_assoc = next(
        a
        for a in eda["covariate_dropout_associations"]
        if a.get("covariate") == "blinded(vs open label)"
    )
    dropout_by_blinding = {
        "BLINDED": float(blind_assoc["blinded_mean_dropout"]),
        "OPEN_LABEL": float(blind_assoc["open_mean_dropout"]),
    }

    # Reason mix over the ratified controlled vocab (already excludes censoring and includes
    # STUDY_TERMINATED + ADMINISTRATIVE — the clean stage drops censoring from the parquet).
    reason_mix = {str(k): float(v) for k, v in eda["withdrawal_reason_mix"].items()}

    covariate_signs = _covariate_signs_from_eda(eda["covariate_dropout_associations"])

    # Composite-stratum scaffold (enrollment / arm-count means per phase x area x sponsor_class).
    # Restricted to trials with positive flow (sum started > 0) — exactly the trials the
    # generator emits participants for — so target 6 grades like-for-like. The REAL marginal
    # targets above are what calibration grades the dropout rates against.
    started = arms.groupby("nct_id")["started"].sum()
    flow_ncts = set(started[started > 0].index)
    scaffold = trials[trials["nct_id"].isin(flow_ncts)]
    by_stratum: dict[str, float] = {}
    enroll_by_stratum: dict[str, float] = {}
    arms_by_stratum: dict[str, float] = {}
    merged = arms.merge(
        trials[["nct_id", "phase", "therapeutic_area", "sponsor_class"]],
        on="nct_id",
        how="left",
    )
    for (phase, area, sclass), g in merged.groupby(
        ["phase", "therapeutic_area", "sponsor_class"]
    ):
        key = stratum_key(str(phase), str(area), str(sclass))
        by_stratum[key] = _weighted_dropout(g)
        sub = scaffold[
            (scaffold["phase"] == phase)
            & (scaffold["therapeutic_area"] == area)
            & (scaffold["sponsor_class"] == sclass)
        ]
        if sub.empty:
            continue
        enroll_by_stratum[key] = float(sub["enrollment"].mean())
        arms_by_stratum[key] = float(sub["n_arms"].mean())

    # Target 4: dropout timing early-vs-late. ASSUMPTION (not a fitted value): AACT
    # aggregates lack per-event timing, so 0.6 is an *assumed* share of dropout in the
    # first half of the planned duration, NOT estimated from the data. Labelled as an
    # assumption in the synthetic manifest and the calibration report.
    # TODO: revisit early_fraction if a real per-event timing source becomes available
    early_fraction = 0.6

    provenance = {
        "source": "captured real EDA over validated ref_* tables",
        "eda_summary_path": str(eda_path),
        "aact_source": eda.get("source"),
        "study_count_post_filter": eda.get("study_count_post_filter"),
        "trials_with_flow_arms": eda.get("trials_with_flow_arms"),
        "note": "Real marginal/overall/reason-mix/sign targets are EDA values (not re-extracted). "
        "early_fraction is an ASSUMPTION (AACT has no per-event timing).",
    }

    return CalibrationTargets(
        overall_dropout_rate=overall,
        overall_dropout_rate_participant_weighted=overall_participant_weighted,
        dropout_by_phase=dropout_by_phase,
        dropout_by_therapeutic_area=dropout_by_area,
        dropout_by_sponsor_class=dropout_by_sponsor_class,
        dropout_by_blinding=dropout_by_blinding,
        dropout_by_site_count=dropout_by_site_count,
        dropout_by_enrollment_band=dropout_by_band,
        reason_mix=reason_mix,
        early_fraction=early_fraction,
        covariate_signs=covariate_signs,
        enrollment_by_stratum=enroll_by_stratum,
        arm_count_by_stratum=arms_by_stratum,
        dropout_by_stratum=by_stratum,
        provenance=provenance,
        strata=sorted(by_stratum.keys()),
    )


def enrollment_band(enrollment: float) -> str:
    """Map an enrollment count to the EDA enrollment-size band label."""
    for label, lo, hi in ENROLLMENT_BANDS:
        if lo < enrollment <= hi:
            return label
    return ENROLLMENT_BANDS[0][0]
