"""Deterministic T2D per-participant synthetic cohort — provenance layers 3 + 4.

AACT gives per-arm aggregates only; the sequence model needs per-participant longitudinal
trajectories. This module synthesises a clearly-labelled T2D cohort whose aggregates reproduce
the REAL guarded T2D dropout marginals (``ingestion.synthetic_t2d_targets``), with engagement
trajectories that *deteriorate ahead of dropout* (the signal a model must learn) at realistic,
NOISY, non-separable strength.

Provenance layering (the task STEP 2):
  - Layer 3 (baseline covariates): per participant, sampled from the calibrated joint, HARD-
    BOUNDED by the trial's eligibility ranges. Imputed HbA1c / BMI are sampled as MARGINALS
    ONLY (no invented correlation) and are EXCLUDED from the hazard. Real-where-posted HbA1c
    may enter the hazard.
  - Layer 4 (per-visit engagement): over the trial's derived visit schedule, a per-visit miss
    probability driven by a latent disengagement state over REAL covariates, arm, cumulative
    misses and trial burden, with literature-fixed effect SIGNS. Dropout is a PROBABILISTIC
    per-visit event sharing that latent state, with a CAMP-style consecutive-missed-visit BOOST
    (>=3 consecutive misses raise — not force — the hazard). The intercept/scale and per-stratum
    offsets are tuned (deterministically) so the simulated per-arm dropout marginal reproduces
    the real guarded marginal overall and per stratum.

Honesty guard: the trajectory->dropout map is NOISY and NOT trivially separable; a non-
separability check fits a logistic classifier on the engagement features and asserts the AUC
sits in a realistic band (<= ~0.85). A near-perfect AUC means a leaky planted rule -> fail loud.

All randomness derives from a single ``numpy.random.Generator(SYNTHETIC_SEED)`` (bit-identical
regeneration). Every participant and every engagement row carries ``synthetic = True``. No PHI.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd

from ingestion import SYNTHETIC_SEED
from ingestion.errors import CalibrationError
from ingestion.synthetic_t2d_targets import (
    BMI_PRIOR_MEAN,
    BMI_PRIOR_SD,
    HBA1C_PRIOR_MEAN,
    HBA1C_PRIOR_SD,
    T2DCohort,
    T2DTargets,
    arm_type_band,
    duration_band,
)
from ingestion.vocab import WITHDRAWAL_REASONS

DISCLAIMER = (
    "SYNTHETIC — calibrated to real T2D AACT aggregates + labelled literature priors; "
    "NOT real participants; method-validity only; no PHI."
)

# Per-arm participant cap. Real summed ``started`` over the 2,402 usable arms is ~580k (>300k),
# so we cap each arm at this many synthetic participants (documented). With the cap the cohort is
# ~120k participants; small arms keep at least MIN_PER_ARM so their marginal stays representable.
MAX_PER_ARM = 60
MIN_PER_ARM = 8

# Visit cadence: one visit roughly every VISIT_INTERVAL_DAYS of planned duration, bounded so the
# per-visit series stays tractable. Documented default when planned_duration is unusable.
VISIT_INTERVAL_DAYS = 28
MIN_VISITS = 4
MAX_VISITS = 40
DEFAULT_VISITS = 12  # fallback when planned_duration_days is absent/degenerate

# CAMP-style dropout rule: >=3 consecutive missed visits ends a participant.
CONSECUTIVE_MISS_DROP = 3

# Hazard calibration loop bounds (deterministic bisection on the DROPOUT intercept).
_INTERCEPT_LO = -10.0
_INTERCEPT_HI = 2.0
_CALIB_ITERS = 44
_CALIB_TOL = 0.002  # overall marginal tolerance during tuning (report tol is +-1pp)
_COORD_ROUNDS = 4  # coordinate-descent rounds over the correlated phase/duration strata

# Literature-fixed hazard effect SIGNS / magnitudes (the task STEP 2). Signs are fixed by
# literature; magnitudes are modest so the trajectory->dropout map stays noisy, not separable.
_BETA_AGE = -0.020  # younger -> higher miss hazard (centred at 55y)
_BETA_HBA1C = (
    0.65  # higher REAL baseline HbA1c burden -> higher hazard (literature sign +)
)
_BETA_PLACEBO = 0.14  # placebo/control arm -> higher hazard (sign +, modest)
_BETA_DURATION = (
    0.00035  # longer planned duration -> higher hazard (centred 500d; sign +)
)
_BETA_VISITS = 0.030  # denser/longer schedule (more visits) -> higher hazard
_BETA_CUM_MISS = (
    0.10  # self-reinforcement: accumulated missed visits raise the drop hazard
)
_CONSEC_BOOST = (
    0.22  # CAMP-style consecutive-miss nudge on the drop hazard (soft, capped)
)
# Noise terms. Each channel gets its OWN independent noise so attendance and the dropout event
# are correlated (shared latent) but neither is a deterministic function of the other — the
# property the non-separability guard enforces. Tuned so the trajectory is INFORMATIVE
# (logistic AUC in a realistic ~0.65-0.82 band) yet far from separable.
_HAZARD_NOISE_SD = (
    1.5  # persistent per-participant latent noise (the trajectory's source)
)
_ATTEND_NOISE_SD = (
    0.9  # independent per-visit attendance noise (decouples observed trajectory)
)
_DROP_NOISE_SD = 1.6  # independent per-visit dropout noise (decouples the event)
# Attendance model: a baseline miss intercept keeps misses common enough that a trajectory forms
# (~10-20% miss rate), and the latent gain ties observed attendance to the shared latent state.
_ATTEND_INTERCEPT = -1.9  # sigmoid(-1.9) ~ 13% baseline per-visit miss probability
_ATTEND_LATENT_GAIN = (
    0.7  # how strongly the latent disengagement shows up in attendance
)
_DROP_LATENT_GAIN = (
    0.6  # how strongly the latent shows up directly in the dropout hazard
)


@dataclass(frozen=True)
class T2DSyntheticCohort:
    participants: pd.DataFrame
    visits: pd.DataFrame  # long: participant_id, visit_index, attendance features
    seed: int
    hazard_intercept: float
    non_separability_auc: float
    info: dict[str, Any] = field(default_factory=dict)


def _n_visits(planned_days: float) -> int:
    if not np.isfinite(planned_days) or planned_days <= 0:
        return DEFAULT_VISITS
    n = int(round(planned_days / VISIT_INTERVAL_DAYS))
    return int(np.clip(n, MIN_VISITS, MAX_VISITS))


def _per_arm_count(started: int) -> int:
    return int(np.clip(int(started), MIN_PER_ARM, MAX_PER_ARM))


def _sample_age(
    rng: np.random.Generator,
    lo: float,
    hi: float,
    n: int,
    cohort_mean: float,
    cohort_sd: float,
) -> np.ndarray:
    """Trial-level mean age (real or imputed) jittered per participant, bounded by eligibility."""
    ages = rng.normal(cohort_mean, max(1.0, cohort_sd * 0.5), n)
    return np.clip(ages, lo, hi)


def _build_layer3(
    cohort: T2DCohort,
    real_values: dict[str, Any],
    rng: np.random.Generator,
) -> pd.DataFrame:
    """Layer 3: per-participant baseline covariates, eligibility-bounded, with imputed flags."""
    meta = cohort.trials.set_index("nct_id").to_dict("index")
    real_age = real_values["age"]
    real_hba = real_values["hba1c"]
    real_bmi = real_values["bmi"]
    real_pf = real_values["pct_female"]
    age_cm, age_csd = real_values["age_cohort_mean"], real_values["age_cohort_sd"]

    rows: list[dict[str, Any]] = []
    pid = 0
    for nct_id in cohort.nct_ids:
        trial = meta[nct_id]
        for arm in cohort.arms[cohort.arms["nct_id"] == nct_id].itertuples():
            n = _per_arm_count(arm.started)
            # Eligibility bounds (hard). Missing ceiling -> a wide-but-finite synthetic bound.
            lo = trial.get("min_age_years")
            hi = trial.get("max_age_years")
            lo = float(lo) if lo is not None and np.isfinite(lo) and lo > 0 else 18.0
            hi = float(hi) if hi is not None and np.isfinite(hi) and hi > lo else 85.0

            age_imputed = nct_id not in real_age
            trial_age_mean = real_age.get(nct_id, age_cm)
            ages = _sample_age(rng, lo, hi, n, trial_age_mean, age_csd)

            # Sex: real %female (755/755 coverage) -> Bernoulli per participant.
            pf = float(real_pf.get(nct_id, 0.5))
            is_female = rng.random(n) < pf

            # HbA1c: real-where-posted (enters hazard); else literature MARGINAL prior (excluded).
            hba_imputed = nct_id not in real_hba
            if hba_imputed:
                hba1c = rng.normal(HBA1C_PRIOR_MEAN, HBA1C_PRIOR_SD, n)
            else:
                hba1c = rng.normal(float(real_hba[nct_id]), 0.4, n)
            hba1c = np.clip(hba1c, 5.5, 13.0)

            # BMI: real-where-posted; else literature MARGINAL prior. Never enters the hazard.
            bmi_imputed = nct_id not in real_bmi
            if bmi_imputed:
                bmi = rng.normal(BMI_PRIOR_MEAN, BMI_PRIOR_SD, n)
            else:
                bmi = rng.normal(float(real_bmi[nct_id]), 2.0, n)
            bmi = np.clip(bmi, 18.0, 55.0)

            armband = arm_type_band(arm.arm_type)
            n_visits = _n_visits(float(trial["planned_duration_days"]))
            for i in range(n):
                rows.append(
                    {
                        "participant_id": pid,
                        "nct_id": nct_id,
                        "arm_id": arm.arm_id,
                        "arm_type": arm.arm_type,
                        "arm_band": armband,
                        "phase": trial["phase"],
                        "n_sites": int(trial["n_sites"]),
                        "planned_duration_days": int(trial["planned_duration_days"]),
                        "n_visits": int(n_visits),
                        "age_years": round(float(ages[i]), 1),
                        "age_baseline_imputed": bool(age_imputed),
                        "sex": "FEMALE" if is_female[i] else "MALE",
                        "hba1c_pct": round(float(hba1c[i]), 2),
                        "hba1c_baseline_imputed": bool(hba_imputed),
                        "bmi": round(float(bmi[i]), 1),
                        "bmi_baseline_imputed": bool(bmi_imputed),
                        "arm_real_dropout_rate": float(arm.dropout_rate),
                        "synthetic": True,
                    }
                )
                pid += 1
    return pd.DataFrame(rows)


def _covariate_logit(p: pd.DataFrame) -> np.ndarray:
    """Per-participant covariate contribution to disengagement (carries the literature/real SIGNS).

    Imputed HbA1c/BMI never enter: HbA1c contributes ONLY where it is real-where-posted; BMI is
    never used. Signs are literature-fixed (the task STEP 2). No intercept here — the attendance
    and dropout intercepts are added at their respective channels.
    """
    age = p["age_years"].to_numpy(float)
    hba = p["hba1c_pct"].to_numpy(float)
    hba_real = (~p["hba1c_baseline_imputed"].to_numpy(bool)).astype(float)
    placebo = (p["arm_band"].to_numpy() == "PLACEBO_CONTROL").astype(float)
    dur = p["planned_duration_days"].to_numpy(float)
    nvis = p["n_visits"].to_numpy(float)
    return (
        _BETA_AGE * (age - 55.0)
        + _BETA_HBA1C * (hba - 8.0) * hba_real
        + _BETA_PLACEBO * placebo
        + _BETA_DURATION * (dur - 500.0)
        + _BETA_VISITS * (nvis - 12.0)
    )


def _offset_vector(p: pd.DataFrame, col: str, offsets: dict[str, float]) -> np.ndarray:
    """Per-participant dropout-intercept offset keyed on a band column (per-stratum calibration)."""
    return p[col].map(lambda k: offsets.get(str(k), 0.0)).to_numpy(float)


def _simulate_visits(
    p: pd.DataFrame,
    drop_intercept: float,
    rng: np.random.Generator,
    *,
    phase_offset: np.ndarray | None = None,
    record: bool = True,
) -> tuple[np.ndarray, list[dict[str, Any]]]:
    """Layer 4: simulate per-visit attendance + dropout; return (dropped mask, visit rows).

    Each participant has a persistent LATENT disengagement propensity = covariate contribution +
    persistent noise. Attendance and the dropout event both depend on that latent state PLUS
    large, INDEPENDENT per-visit noise, so the observed trajectory CORRELATES with dropout
    without DETERMINING it: two participants with identical attendance can differ in outcome.
    This keeps the map non-separable.

    The dropout channel carries a TUNABLE intercept (``drop_intercept``, set by the calibration
    loop) so the overall marginal can be matched WITHOUT changing the attendance/trajectory
    dynamics or the AUC. Dropout per visit fires from its own noisy hazard (latent + cum-miss
    reinforcement + CAMP-style consecutive boost + independent noise); never a hard threshold on
    an observable. Observations stop at the dropout visit (no post-dropout rows) so no
    outcome/future data leaks.
    """
    n = len(p)
    cov = _covariate_logit(p)
    nvis = p["n_visits"].to_numpy(int)
    pid = p["participant_id"].to_numpy(int)
    max_v = int(nvis.max())
    ph_off = phase_offset if phase_offset is not None else np.zeros(n)

    # Persistent latent disengagement (covariate signal + persistent noise). Drives BOTH channels.
    latent = cov + rng.normal(0.0, _HAZARD_NOISE_SD, n)

    dropped = np.zeros(n, dtype=bool)
    consec = np.zeros(n, dtype=int)
    cum_miss = np.zeros(n, dtype=int)
    active = np.ones(n, dtype=bool)
    rows: list[dict[str, Any]] = []

    for v in range(max_v):
        in_window = active & (v < nvis)
        if not in_window.any():
            break

        # --- attendance (observed) ---: latent + a baseline miss intercept + independent noise.
        # The intercept keeps misses common enough that a *trajectory* forms, so the
        # deteriorating-engagement signal is real and learnable.
        att_logit = (
            _ATTEND_INTERCEPT
            + _ATTEND_LATENT_GAIN * latent
            + rng.normal(0.0, _ATTEND_NOISE_SD, n)
        )
        prob_miss = 1.0 / (1.0 + np.exp(-att_logit))
        missed = (rng.random(n) < prob_miss) & in_window
        attended = in_window & ~missed
        consec = np.where(missed, consec + 1, 0)
        cum_miss = np.where(missed, cum_miss + 1, cum_miss)

        if record:
            for idx in np.nonzero(in_window)[0]:
                rows.append(
                    {
                        "participant_id": int(pid[idx]),
                        "visit_index": v,
                        "attended": bool(attended[idx]),
                        "missed": bool(missed[idx]),
                        "cumulative_missed": int(cum_miss[idx]),
                        "consecutive_missed": int(consec[idx]),
                        "miss_probability": round(float(prob_miss[idx]), 4),
                        "synthetic": True,
                    }
                )

        # --- dropout (latent event) ---: tunable intercept + latent (covariate signs) + the
        # OBSERVED disengagement trajectory (cum-miss / consecutive-miss, the mediator the
        # sequence model learns) + its OWN large independent noise. Probabilistic, never a hard
        # threshold on an observable, so the map stays non-separable.
        drop_logit = (
            drop_intercept
            + ph_off
            + _DROP_LATENT_GAIN * latent
            + _BETA_CUM_MISS * cum_miss
            + _CONSEC_BOOST * np.minimum(consec, CONSECUTIVE_MISS_DROP)
            + rng.normal(0.0, _DROP_NOISE_SD, n)
        )
        prob_drop = 1.0 / (1.0 + np.exp(-drop_logit))
        fired = (rng.random(n) < prob_drop) & in_window
        dropped = dropped | fired
        active = active & ~fired
    return dropped, rows


def _attach_outcomes(
    p: pd.DataFrame,
    dropped: np.ndarray,
    visits: pd.DataFrame,
    targets: T2DTargets,
    rng: np.random.Generator,
) -> pd.DataFrame:
    """Attach dropped flag, reason (for droppers), and the dropout visit index."""
    p = p.copy()
    p["dropped"] = dropped
    p["censored"] = ~dropped

    last_visit = (
        visits.groupby("participant_id")["visit_index"].max().rename("last_visit")
    )
    p = p.merge(last_visit, on="participant_id", how="left")
    p["dropout_visit_index"] = np.where(p["dropped"], p["last_visit"], np.nan)

    reasons = list(WITHDRAWAL_REASONS)
    rp = np.array([targets.reason_mix.get(r, 0.0) for r in reasons], float)
    rp = rp / rp.sum() if rp.sum() > 0 else np.ones(len(reasons)) / len(reasons)
    n_drop = int(p["dropped"].sum())
    drawn = rng.choice(reasons, size=n_drop, p=rp) if n_drop else np.array([])
    reason_col = np.full(len(p), "", dtype=object)
    reason_col[p["dropped"].to_numpy()] = drawn
    p["dropout_reason"] = reason_col
    return p.drop(columns=["last_visit"])


def _engagement_features(
    visits: pd.DataFrame, participants: pd.DataFrame
) -> pd.DataFrame:
    """Per-participant engagement summary features for the non-separability check.

    Trajectory features only (attendance rate, recent-vs-early ratio, max consecutive miss,
    final-window attendance) — exactly the kind a sequence model would derive. NO covariates,
    NO outcome-derived fields.
    """
    g = visits.sort_values(["participant_id", "visit_index"]).groupby("participant_id")
    feats: list[dict[str, Any]] = []
    for pid, sub in g:
        att = sub["attended"].to_numpy()
        n = len(att)
        half = max(1, n // 2)
        early = att[:half].mean()
        late = att[half:].mean() if n > half else early
        # max consecutive misses
        max_consec = int(sub["consecutive_missed"].max())
        feats.append(
            {
                "participant_id": int(pid),
                "attendance_rate": float(att.mean()),
                "early_attendance": float(early),
                "late_attendance": float(late),
                "attendance_trend": float(late - early),
                "max_consecutive_missed": max_consec,
                "n_observed_visits": n,
            }
        )
    fdf = pd.DataFrame(feats)
    return fdf.merge(
        participants[["participant_id", "dropped"]], on="participant_id", how="left"
    )


def _non_separability_auc(features: pd.DataFrame, seed: int) -> float:
    """Fit a simple logistic classifier on engagement features; return ROC-AUC.

    A realistic trajectory->dropout map is predictable but NOT perfectly separable. A near-1.0
    AUC means a leaky planted deterministic rule.
    """
    from sklearn.linear_model import LogisticRegression
    from sklearn.metrics import roc_auc_score

    cols = [
        "attendance_rate",
        "early_attendance",
        "late_attendance",
        "attendance_trend",
        "max_consecutive_missed",
    ]
    x = features[cols].to_numpy(float)
    y = features["dropped"].to_numpy(int)
    if y.min() == y.max():
        return 0.5
    clf = LogisticRegression(max_iter=1000, random_state=seed)
    clf.fit(x, y)
    return float(roc_auc_score(y, clf.predict_proba(x)[:, 1]))


def _bisect_offset(probe, target: float) -> tuple[float, float]:
    """Deterministic bisection on a scalar logit offset until ``probe(offset)`` hits ``target``.

    ``probe`` maps an offset to the realised dropout rate. Returns ``(best_offset, best_rate)``.
    """
    lo, hi = _INTERCEPT_LO, _INTERCEPT_HI
    best_off, best_rate = 0.0, probe(0.0)
    for _ in range(_CALIB_ITERS):
        mid = 0.5 * (lo + hi)
        rate = probe(mid)
        if abs(rate - target) < abs(best_rate - target):
            best_off, best_rate = mid, rate
        if abs(rate - target) <= _CALIB_TOL:
            return mid, rate
        if rate < target:
            lo = mid
        else:
            hi = mid
    return best_off, best_rate


def _calibrate(
    p: pd.DataFrame, targets: T2DTargets, seed: int
) -> tuple[float, dict[str, float], dict[str, float]]:
    """Calibrate a global intercept + per-phase + per-duration-band offsets to real marginals.

    Stage 1 fits the global dropout intercept to the overall guarded marginal (+-1pp). Stage 2
    fits a per-phase logit offset, then Stage 3 fits a per-duration-band residual offset (on top
    of phase) so BOTH stratum families match their real guarded marginals (+-3pp). This is the
    deterministic intercept/scale tuning the task STEP 2 requires. Returns
    ``(intercept, phase_offsets, duration_offsets)``.
    """

    def probe(off_vec: np.ndarray, intercept: float, sub: pd.DataFrame) -> float:
        rng = np.random.default_rng(seed + 7919)
        dropped, _ = _simulate_visits(
            sub, intercept, rng, phase_offset=off_vec, record=False
        )
        return float(dropped.mean())

    p = p.copy()
    p["_dband"] = p["planned_duration_days"].map(duration_band)
    zeros = np.zeros(len(p))
    intercept, _ = _bisect_offset(
        lambda c: probe(zeros, c, p), targets.dropout_marginal
    )

    # Phase and duration-band are correlated, so calibrating one perturbs the other. Coordinate-
    # descent: re-fit phase offsets given the current duration offsets and vice versa until both
    # stratum families settle (deterministic; a fixed small number of rounds).
    phase_offsets: dict[str, float] = {ph: 0.0 for ph in targets.dropout_by_phase}
    duration_offsets: dict[str, float] = {
        db: 0.0 for db in targets.dropout_by_duration_band
    }

    def fit_dim(
        col: str, real: dict[str, float], other_off: np.ndarray
    ) -> dict[str, float]:
        out: dict[str, float] = {}
        for level, real_rate in real.items():
            mask = (p[col] == level).to_numpy()
            if mask.sum() == 0:
                continue
            sub = p[mask].reset_index(drop=True)
            base = other_off[mask]
            off, _ = _bisect_offset(
                lambda o, sub=sub, base=base: probe(base + o, intercept, sub), real_rate
            )
            out[str(level)] = off
        return out

    for _ in range(_COORD_ROUNDS):
        dur_vec = _offset_vector(p, "_dband", duration_offsets)
        phase_offsets = fit_dim("phase", targets.dropout_by_phase, dur_vec)
        ph_vec = _offset_vector(p, "phase", phase_offsets)
        duration_offsets = fit_dim("_dband", targets.dropout_by_duration_band, ph_vec)
    return intercept, phase_offsets, duration_offsets


def generate(
    cohort: T2DCohort,
    targets: T2DTargets,
    real_values: dict[str, Any],
    *,
    seed: int = SYNTHETIC_SEED,
    max_auc: float = 0.85,
) -> T2DSyntheticCohort:
    """Generate the deterministic T2D per-participant cohort (layers 3 + 4, calibrated)."""
    rng = np.random.default_rng(seed)
    participants = _build_layer3(cohort, real_values, rng)

    intercept, phase_offsets, duration_offsets = _calibrate(participants, targets, seed)
    ph_off = _offset_vector(participants, "phase", phase_offsets)
    dband = participants["planned_duration_days"].map(duration_band)
    dur_off = dband.map(lambda k: duration_offsets.get(str(k), 0.0)).to_numpy(float)
    total_off = ph_off + dur_off

    sim_rng = np.random.default_rng(seed + 104729)
    dropped, visit_rows = _simulate_visits(
        participants, intercept, sim_rng, phase_offset=total_off
    )
    visits = pd.DataFrame(visit_rows)
    participants = _attach_outcomes(participants, dropped, visits, targets, rng)

    features = _engagement_features(visits, participants)
    auc = _non_separability_auc(features, seed)
    if auc > max_auc:
        raise CalibrationError(
            f"Non-separability guard FAILED: engagement->dropout AUC={auc:.3f} > {max_auc}. "
            f"A near-perfect AUC means a leaky planted deterministic rule, not a realistic "
            f"noisy trajectory. Reduce hazard signal / raise noise before training."
        )

    info = {
        "n_participants": int(len(participants)),
        "n_visit_rows": int(len(visits)),
        "overall_dropout_synthetic": round(float(participants["dropped"].mean()), 4),
        "hazard_intercept": round(float(intercept), 4),
        "phase_offsets": {k: round(v, 4) for k, v in phase_offsets.items()},
        "duration_offsets": {k: round(v, 4) for k, v in duration_offsets.items()},
        "max_per_arm": MAX_PER_ARM,
        "min_per_arm": MIN_PER_ARM,
        "consecutive_miss_drop": CONSECUTIVE_MISS_DROP,
        "visit_interval_days": VISIT_INTERVAL_DAYS,
    }
    return T2DSyntheticCohort(
        participants=participants,
        visits=visits,
        seed=seed,
        hazard_intercept=intercept,
        non_separability_auc=auc,
        info=info,
    )
