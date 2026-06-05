"""Deterministic synthetic per-participant cohort, calibrated to AACT aggregates.

AACT gives aggregates only (per-arm counts). The DL layer needs per-participant
longitudinal sequences, so we synthesise a clearly-labelled cohort whose aggregates
reproduce the real statistics. It proves the METHOD; it is never a clinical claim.

All randomness derives from a single ``numpy.random.Generator(SYNTHETIC_SEED)`` so
regeneration is bit-identical. Every row carries ``synthetic = True``. Output lives under
``data/synthetic/`` with a README disclaimer.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from ingestion import SYNTHETIC_SEED
from ingestion.config import SYNTHETIC_ROOT
from ingestion.targets import CalibrationTargets, stratum_key
from ingestion.vocab import WITHDRAWAL_REASONS

# Engagement diary cadence: one observation per day of planned enrollment, capped so the
# series stays tractable. The signal is the *trend*, not the level.
_MAX_SERIES_DAYS = 180

DISCLAIMER = (
    "SYNTHETIC — calibrated to aggregate AACT statistics, NOT real participants, "
    "method-validity only, no PHI."
)

# Labelled assumptions for covariates AACT does not provide (recorded in the manifest).
COVARIATE_ASSUMPTIONS = {
    "baseline_severity": "Standardised 0-100 score; drawn per phase (later phase -> "
    "broader severity). Calibrated to published trial demographics, not AACT.",
    "socioeconomic_proxy": "Labelled synthetic 0-1 proxy; no real SES data exists in "
    "AACT.",
    "travel_friction": "0-1 proxy derived from trial n_sites (multi-site -> lower "
    "friction). Synthetic.",
    "prior_trial_experience": "Poisson count; synthetic, no AACT source.",
    "comorbidity_count": "Poisson count scaled by therapeutic area; synthetic.",
    "age_years": "Sampled within trial [min_age, max_age] when present, else 18-75.",
    "early_fraction": "ASSUMPTION (not fitted): assumed share of dropout in the first "
    "half of planned duration. AACT aggregates lack per-event timing, so this is set by "
    "assumption (0.6), not estimated from data.",
}


@dataclass(frozen=True)
class SyntheticCohort:
    participants: pd.DataFrame
    engagement: pd.DataFrame  # long format: participant_id, day, features...
    seed: int


def _participant_age(
    rng: np.random.Generator, lo: float | None, hi: float | None
) -> float:
    low = lo if lo and lo > 0 else 18.0
    high = hi if hi and hi > low else 75.0
    return float(round(rng.uniform(low, high), 1))


def _arm_dropout_rates(arms: pd.DataFrame) -> dict[str, float]:
    return {row.arm_id: float(row.dropout_rate) for row in arms.itertuples()}


def generate(
    trials: pd.DataFrame,
    arms: pd.DataFrame,
    targets: CalibrationTargets,
    *,
    seed: int = SYNTHETIC_SEED,
) -> SyntheticCohort:
    """Sample a per-participant cohort whose aggregates reproduce ``targets``."""
    rng = np.random.default_rng(seed)
    arm_rate = _arm_dropout_rates(arms)
    arms_by_trial: dict[str, list] = {}
    for row in arms.itertuples():
        arms_by_trial.setdefault(row.nct_id, []).append(row)

    trial_meta = trials.set_index("nct_id").to_dict("index")

    p_rows: list[dict] = []
    e_rows: list[dict] = []
    pid = 0

    # Global reason distribution to draw a dropout reason per dropping participant.
    reasons = list(WITHDRAWAL_REASONS)
    reason_p = np.array([targets.reason_mix.get(r, 0.0) for r in reasons], float)
    if reason_p.sum() <= 0:
        reason_p = np.ones(len(reasons))
    reason_p = reason_p / reason_p.sum()

    for nct_id, trial in trial_meta.items():
        trial_arms = arms_by_trial.get(nct_id, [])
        if not trial_arms:
            continue
        enrollment = int(trial["enrollment"])
        # Distribute enrollment across arms proportional to each arm's started count.
        started_counts = np.array([a.started for a in trial_arms], float)
        if started_counts.sum() <= 0:
            continue
        weights = started_counts / started_counts.sum()
        per_arm_n = _largest_remainder(enrollment, weights)

        planned = int(trial["planned_duration_days"])
        series_days = int(min(_MAX_SERIES_DAYS, max(14, planned)))

        for arm, n in zip(trial_arms, per_arm_n, strict=True):
            if n <= 0:
                continue
            rate = arm_rate[arm.arm_id]
            # Pass 1: draw covariates and a covariate-driven dropout score per participant.
            # The score preserves the spec's sign associations (longer duration / more
            # sites / higher severity -> higher dropout). We then select EXACTLY
            # round(rate * n) participants as droppers, which holds the arm mean at the
            # target (so the overall and per-stratum dropout calibrations pass) while the
            # covariate ordering survives (so the sign associations pass).
            arm_covars = [_draw_covariates(rng, trial) for _ in range(n)]
            scores = np.array(
                [
                    0.0009 * (planned - 600)
                    + 0.05 * (c["n_sites"] - 10)
                    + 2.0 * (c["baseline_severity"] / 100 - 0.5)
                    + rng.normal(0, 0.5)
                    for c in arm_covars
                ]
            )
            n_drop = int(round(rate * n))
            drop_mask = np.zeros(n, dtype=bool)
            if n_drop > 0:
                drop_idx = np.argsort(-scores)[:n_drop]
                drop_mask[drop_idx] = True

            for j in range(n):
                covars = arm_covars[j]
                dropped = bool(drop_mask[j])
                if dropped:
                    if rng.random() < targets.early_fraction:
                        t_drop = int(rng.integers(1, max(2, series_days // 2)))
                    else:
                        t_drop = int(
                            rng.integers(max(2, series_days // 2), series_days + 1)
                        )
                    reason = str(rng.choice(reasons, p=reason_p))
                else:
                    t_drop = series_days
                    reason = ""

                p_rows.append(
                    {
                        "participant_id": pid,
                        "nct_id": nct_id,
                        "arm_id": arm.arm_id,
                        "arm_type": arm.arm_type,
                        "phase": trial["phase"],
                        "therapeutic_area": trial["therapeutic_area"],
                        "sponsor_class": trial["sponsor_class"],
                        "masking": trial["masking"],
                        "n_sites": int(trial["n_sites"]),
                        "planned_duration_days": planned,
                        "stratum": stratum_key(
                            str(trial["phase"]),
                            str(trial["therapeutic_area"]),
                            str(trial["sponsor_class"]),
                        ),
                        **covars,
                        "dropped": dropped,
                        "time_to_event_days": t_drop,
                        "censored": (not dropped),
                        "dropout_reason": reason,
                        "synthetic": True,
                    }
                )
                _emit_engagement(e_rows, rng, pid, series_days, t_drop, dropped, covars)
                pid += 1

    participants = pd.DataFrame(p_rows)
    engagement = pd.DataFrame(e_rows)
    return SyntheticCohort(participants=participants, engagement=engagement, seed=seed)


def _draw_covariates(rng: np.random.Generator, trial: dict) -> dict:
    phase_idx = {"Phase 1": 0, "Phase 2": 1, "Phase 3": 2, "Phase 4": 3}.get(
        str(trial["phase"]), 1
    )
    severity = float(np.clip(rng.normal(40 + 8 * phase_idx, 15), 0, 100))
    n_sites = int(trial["n_sites"])
    travel_friction = float(
        np.clip(1.0 - min(n_sites, 30) / 30 + rng.normal(0, 0.1), 0, 1)
    )
    return {
        "age_years": _participant_age(
            rng, trial.get("min_age_years"), trial.get("max_age_years")
        ),
        "baseline_severity": round(severity, 2),
        "socioeconomic_proxy": round(float(rng.beta(2, 2)), 4),
        "travel_friction": round(travel_friction, 4),
        "prior_trial_experience": int(rng.poisson(0.7)),
        "comorbidity_count": int(rng.poisson(1.5)),
        "n_sites": n_sites,
    }


def _emit_engagement(
    e_rows: list[dict],
    rng: np.random.Generator,
    pid: int,
    series_days: int,
    t_drop: int,
    dropped: bool,
    covars: dict,
) -> None:
    """Daily engagement series whose trajectory deteriorates ahead of dropout.

    Observations stop at the last entry before the event: a dropping participant goes
    silent at ``t_drop`` (no post-dropout rows), so no future/outcome data leaks.
    """
    last_obs = t_drop if dropped else series_days
    base_completion = float(np.clip(0.9 - covars["travel_friction"] * 0.2, 0.4, 0.98))
    for day in range(last_obs):
        if dropped:
            # Linear decline ramping into the event; noisy.
            progress = day / max(1, t_drop)
            decay = 1.0 - 0.7 * progress
        else:
            decay = 1.0 - 0.05 * (day / max(1, series_days))
        completion = float(np.clip(base_completion * decay + rng.normal(0, 0.05), 0, 1))
        app_opens = int(max(0, rng.poisson(max(0.1, 3 * decay))))
        latency = float(np.clip(rng.normal(6 + 18 * (1 - decay), 4), 0.1, 96))
        symptom_logs = int(max(0, rng.poisson(max(0.1, 2 * decay))))
        e_rows.append(
            {
                "participant_id": pid,
                "day": day,
                "diary_completion": round(completion, 4),
                "app_opens": app_opens,
                "reminder_response_latency_h": round(latency, 2),
                "symptom_log_count": symptom_logs,
                "synthetic": True,
            }
        )


def _largest_remainder(total: int, weights: np.ndarray) -> list[int]:
    raw = weights * total
    floors = np.floor(raw).astype(int)
    remainder = total - int(floors.sum())
    frac_order = np.argsort(-(raw - floors))
    for i in range(remainder):
        floors[frac_order[i % len(floors)]] += 1
    return [int(x) for x in floors]


def write_synthetic(cohort: SyntheticCohort, out_root: Path = SYNTHETIC_ROOT) -> None:
    out_root.mkdir(parents=True, exist_ok=True)
    cohort.participants.to_parquet(out_root / "participants.parquet", index=False)
    cohort.engagement.to_parquet(out_root / "engagement.parquet", index=False)
    (out_root / "README.md").write_text(
        f"# Synthetic cohort\n\n{DISCLAIMER}\n\n"
        f"Seed: {cohort.seed} (bit-identical regeneration). Every row has "
        f"`synthetic = True`.\n",
        encoding="utf-8",
    )
