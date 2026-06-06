"""Deterministic synthetic per-participant cohort, calibrated to REAL AACT aggregates.

AACT gives aggregates only (per-arm counts). The DL layer needs per-participant
longitudinal sequences, so we synthesise a clearly-labelled cohort whose aggregates
reproduce the real statistics. It proves the METHOD; it is never a clinical claim.

The cohort is generated from the REAL ``ref_*`` trials (a stratified subsample), giving every
trial an equal number of participants whose dropper fraction equals that trial's real
participant-weighted rate. Because trials are weighted equally, the cohort reproduces the REAL
TRIAL-MEAN marginal targets simultaneously: by phase, therapeutic_area, sponsor_class, blinding
(blinded vs open), single- vs multi-site (specs/data.md target 2), and the real trial-mean
overall (target 1). Covariates carry the real association SIGNS (target 5): more sites /
countries / longer duration -> higher dropout, open-label > blinded. Reason mix (target 3) is
drawn from the real ratified vocab.

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
from ingestion.targets import CalibrationTargets, enrollment_band, stratum_key
from ingestion.vocab import WITHDRAWAL_REASONS

# Engagement diary cadence: one observation per day of planned enrollment, capped so the
# series stays tractable at build time. The DL signal is the *trend* (the deteriorating
# trajectory ahead of dropout), not the absolute length, so a bounded window suffices.
_MAX_SERIES_DAYS = 90

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
    "assumption (0.6), not estimated from data. See the early_fraction TODO in targets.py.",
    "per_trial_dropout_rate": "Each real trial's dropper fraction equals its real "
    "participant-weighted rate (not_completed/started over its arms). With equal participants "
    "per trial, the cohort reproduces the real TRIAL-MEAN marginal dropout targets (phase, "
    "therapeutic_area, sponsor_class, blinding, site-count) and the real trial-mean overall.",
}


@dataclass(frozen=True)
class SyntheticCohort:
    participants: pd.DataFrame
    engagement: pd.DataFrame  # long format: participant_id, day, features...
    seed: int


# Trials drawn from the real reference set for the synthetic cohort. Stratified so each real
# marginal level keeps enough trials that its trial-mean dropout stays within +-3pp; bounded so
# the build stays tractable (~K participants per trial).
SUBSAMPLE_TRIALS = 8_000


def subsample_trials(
    trials: pd.DataFrame,
    arms: pd.DataFrame,
    withdrawals: pd.DataFrame,
    *,
    n_trials: int = SUBSAMPLE_TRIALS,
    seed: int = SYNTHETIC_SEED,
) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Deterministic stratified subsample of real trials preserving trial-mean marginals.

    Samples within each (sponsor_class, phase, therapeutic_area) cell proportional to the cell
    size (at least one trial per non-empty cell), so the cohort's stratum mix mirrors the real
    population and the trial-mean marginal targets are reproduced. If the reference set is
    already small (e.g. a test fixture), it is returned unchanged.
    """
    if len(trials) <= n_trials:
        return trials, arms, withdrawals
    rng = np.random.default_rng(seed)
    frac = n_trials / len(trials)
    kept: list[str] = []
    for _, g in trials.groupby(["sponsor_class", "phase", "therapeutic_area"]):
        m = max(1, int(round(len(g) * frac)))
        ncts = np.sort(g["nct_id"].to_numpy())
        chosen = rng.choice(ncts, size=min(m, len(ncts)), replace=False)
        kept.extend(chosen.tolist())
    keep = set(kept)
    return (
        trials[trials["nct_id"].isin(keep)].reset_index(drop=True),
        arms[arms["nct_id"].isin(keep)].reset_index(drop=True),
        withdrawals[withdrawals["nct_id"].isin(keep)].reset_index(drop=True),
    )


def _participant_age(
    rng: np.random.Generator, lo: float | None, hi: float | None
) -> float:
    low = lo if (lo is not None and np.isfinite(lo) and lo > 0) else 18.0
    high = (
        hi if (hi is not None and np.isfinite(hi) and hi > low) else max(low + 1, 75.0)
    )
    return float(round(rng.uniform(low, high), 1))


def _blinding_band(masking: str) -> str:
    return "OPEN_LABEL" if str(masking) == "NONE" else "BLINDED"


def _site_band(n_sites: int) -> str:
    return "SINGLE_SITE" if int(n_sites) <= 1 else "MULTI_SITE"


# Participants synthesised per real trial. Equal per trial so the cohort's per-stratum mean is
# the TRIAL-MEAN dropout (each trial weighted equally) — the aggregation the real EDA marginal
# targets are computed on. Small enough to stay tractable, large enough that round(rate*K)
# rounding stays well inside the +-3pp per-stratum tolerance.
PARTICIPANTS_PER_TRIAL = 12


def generate(
    trials: pd.DataFrame,
    arms: pd.DataFrame,
    targets: CalibrationTargets,
    *,
    seed: int = SYNTHETIC_SEED,
    participants_per_trial: int = PARTICIPANTS_PER_TRIAL,
) -> SyntheticCohort:
    """Sample a per-participant cohort whose aggregates reproduce the REAL ``targets``.

    Each real trial contributes ``participants_per_trial`` synthetic participants, and the
    fraction marked as droppers equals that trial's REAL participant-weighted dropout rate
    (``not_completed / started`` over its arms). Because every trial contributes equally, the
    cohort's per-stratum dropout is the real TRIAL-MEAN dropout, reproducing the EDA marginal
    targets directly (no fitting, no fudging). Covariates carry the real association signs.
    """
    rng = np.random.default_rng(seed)
    arms_by_trial: dict[str, list] = {}
    for row in arms.itertuples():
        arms_by_trial.setdefault(row.nct_id, []).append(row)

    # Per-trial real participant-weighted dropout rate from its arms.
    trial_rate: dict[str, float] = {}
    for nct_id, trial_arms in arms_by_trial.items():
        started = sum(int(a.started) for a in trial_arms)
        not_completed = sum(int(a.not_completed) for a in trial_arms)
        trial_rate[nct_id] = (not_completed / started) if started > 0 else 0.0

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

    k = int(participants_per_trial)
    for nct_id, trial in trial_meta.items():
        trial_arms = arms_by_trial.get(nct_id, [])
        if not trial_arms:
            continue
        started_counts = np.array([a.started for a in trial_arms], float)
        if started_counts.sum() <= 0:
            continue
        enrollment = int(trial["enrollment"])
        # K participants per trial, distributed across arms proportional to started counts.
        weights = started_counts / started_counts.sum()
        per_arm_n = _largest_remainder(k, weights)
        arm_of = [
            arm for arm, n in zip(trial_arms, per_arm_n, strict=True) for _ in range(n)
        ]

        planned = int(trial["planned_duration_days"])
        series_days = int(min(_MAX_SERIES_DAYS, max(14, planned)))
        rate = trial_rate[nct_id]

        # Draw covariates and a covariate-driven dropout score per participant. The score
        # preserves the spec's sign associations (longer duration / more sites / more
        # countries / higher severity -> higher dropout). We then select EXACTLY
        # round(rate * k) participants trial-wide as droppers, so the trial's dropout fraction
        # equals its REAL participant-weighted rate (each trial weighted equally -> the cohort's
        # per-stratum mean is the real TRIAL-MEAN, reproducing the marginal targets) while the
        # covariate ordering survives (so the sign associations pass).
        covars_list = [_draw_covariates(rng, trial) for _ in range(k)]
        scores = np.array(
            [
                0.0009 * (planned - 600)
                + 0.05 * (c["n_sites"] - 10)
                + 0.10 * (c["n_countries"] - 3)
                + 2.0 * (c["baseline_severity"] / 100 - 0.5)
                + rng.normal(0, 0.5)
                for c in covars_list
            ]
        )
        n_drop = int(round(rate * k))
        drop_mask = np.zeros(k, dtype=bool)
        if n_drop > 0:
            drop_mask[np.argsort(-scores)[:n_drop]] = True

        for j in range(k):
            covars = covars_list[j]
            arm = arm_of[j]
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
                    "blinding": _blinding_band(trial["masking"]),
                    "n_sites": int(trial["n_sites"]),
                    "site_band": _site_band(int(trial["n_sites"])),
                    "enrollment": enrollment,
                    "n_arms": int(trial.get("n_arms") or len(trial_arms)),
                    "enrollment_band": enrollment_band(float(enrollment)),
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
    phase_idx = {"PHASE1": 0, "PHASE2": 1, "PHASE3": 2, "PHASE4": 3}.get(
        str(trial["phase"]), 1
    )
    severity = float(np.clip(rng.normal(40 + 8 * phase_idx, 15), 0, 100))
    n_sites = int(trial["n_sites"])
    n_countries = int(trial.get("n_countries") or 1)
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
        "n_countries": n_countries,
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


def write_synthetic(
    cohort: SyntheticCohort,
    out_root: Path = SYNTHETIC_ROOT,
    *,
    generation_source: str = "unspecified",
) -> None:
    out_root.mkdir(parents=True, exist_ok=True)
    cohort.participants.to_parquet(out_root / "participants.parquet", index=False)
    cohort.engagement.to_parquet(out_root / "engagement.parquet", index=False)
    (out_root / "README.md").write_text(
        f"# Synthetic cohort\n\n{DISCLAIMER}\n\n"
        f"Generation source: **{generation_source}** "
        f"(`real` = built from the real AACT snapshot; `sample` = derived from the SAMPLE "
        f"fixture, NOT real). A non-`real` cohort must never be treated as real.\n\n"
        f"Seed: {cohort.seed} (bit-identical regeneration). Every row has "
        f"`synthetic = True`.\n",
        encoding="utf-8",
    )
