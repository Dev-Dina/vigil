"""T2D modelling-cohort selection and calibration targets (build-time only).

The Type-2-diabetes modelling cohort is the canonical slice the per-participant synthetic
generator (``ingestion.synthetic_t2d``) is calibrated against. It is defined ONCE here so the
selection predicates, the calibration targets, and the cohort never drift.

Cohort predicate (specs/data.md modelling-cohort scope + the task SCOPE):
  - clean ``ref_trial``: ``sponsor_class == 'INDUSTRY'`` and ``phase`` in the four modelling
    phases ``{PHASE1/PHASE2, PHASE2, PHASE2/PHASE3, PHASE3}``;
  - raw ``browse_conditions``: a MeSH term ILIKE ``'%Diabetes Mellitus, Type 2%'``;
  - raw ``interventions``: an ``intervention_type`` in ``{DRUG, BIOLOGICAL}``;
  - clean ``ref_arm``: at least one USABLE arm (``started > 0`` and ``completed`` not null).
This reproduces EXACTLY 755 trials / 2,402 usable arms; the selector FAILS LOUD otherwise.

Every target value here is REAL (computed from the captured AACT snapshot + the validated
``ref_*`` tables) except the two clearly-labelled literature priors (imputed HbA1c / BMI) and
the assumed dropout-timing share — each carries an explicit ``source`` / provenance tag. These
statistics are computed from outcomes and are used ONLY for calibration, never as a feature
(that would be target leakage, specs/data.md leakage rules).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from ingestion.config import CLEAN_ROOT, DATA_ROOT, RAW_ROOT

# Pinned AACT snapshot (its date is the provenance key, specs/data.md raw ingestion).
SNAPSHOT_DATE = "2026-06-05"

# The four modelling phases (specs/data.md modelling cohort). Reference-only phases excluded.
MODELLING_PHASES = ("PHASE1/PHASE2", "PHASE2", "PHASE2/PHASE3", "PHASE3")

# The exact cohort the selection MUST reproduce. Fail loud on any drift (the task SCOPE).
EXPECTED_TRIALS = 755
EXPECTED_USABLE_ARMS = 2_402

# Dropout-marginal guard (the task STEP 1): the all-lost / tiny arms are label-suspect, so they
# are excluded from EVERY dropout target (marginal + per-stratum). Recorded, not hidden.
MIN_STARTED = 20
DROPOUT_RATE_CEILING = 1.0  # dropout_rate == 1.0 (whole arm lost) is excluded

# Literature priors for the baseline covariates AACT does not post for every trial. These are
# LABELLED ASSUMPTIONS, never presented as real (the task STEP 1 + card).
HBA1C_PRIOR_MEAN = 8.0  # T2D entry HbA1c ~7.5-8.5%; midpoint prior
HBA1C_PRIOR_SD = 0.6
BMI_PRIOR_MEAN = 31.0  # T2D ~30-32 kg/m^2; midpoint prior
BMI_PRIOR_SD = 3.0

# Dropout-timing early share. ASSUMPTION (not fitted): AACT aggregates carry no per-event
# timing, so 0.6 is an assumed share of dropout in the first half of the trial, consistent with
# the whole-cohort generator (ingestion.targets.early_fraction). Carded, never claimed as real.
EARLY_FRACTION_ASSUMED = 0.6

_T2D_MESH_PATTERN = re.compile(r"diabetes mellitus, type 2", re.IGNORECASE)
_DRUG_BIO = ("DRUG", "BIOLOGICAL")


@dataclass(frozen=True)
class CovariateProvenance:
    """Per-covariate provenance + coverage for the baseline marginals (the task STEP 1)."""

    name: str
    source: str  # real_table1 | real_clean | literature_prior
    coverage_pct: float
    mean: float
    sd: float
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "coverage_pct": round(self.coverage_pct, 4),
            "mean": round(self.mean, 4),
            "sd": round(self.sd, 4),
            "note": self.note,
        }


@dataclass(frozen=True)
class T2DCohort:
    """The selected T2D modelling cohort: trial / usable-arm / withdrawal frames."""

    trials: pd.DataFrame
    arms: pd.DataFrame  # USABLE arms only
    withdrawals: pd.DataFrame
    nct_ids: list[str]


@dataclass(frozen=True)
class T2DTargets:
    """Real (+ labelled-prior) calibration targets for the T2D synthetic cohort."""

    dropout_marginal: float  # guarded per-arm mean (the calibrated quantity)
    dropout_marginal_unguarded: float  # recorded alongside
    dropout_by_phase: dict[str, float]
    dropout_by_arm_type: dict[str, float]  # PLACEBO_CONTROL vs ACTIVE/EXPERIMENTAL
    dropout_by_site_band: dict[str, float]  # SINGLE_SITE vs MULTI_SITE
    dropout_by_duration_band: dict[str, float]
    reason_mix: dict[str, float]
    early_fraction_assumed: float
    eligibility: dict[str, Any]  # age min/max, sex
    covariate_provenance: dict[str, dict[str, Any]]
    covariate_signs: dict[
        str, int
    ]  # covariate -> real/literature sign of dropout assoc
    n_trials: int
    n_usable_arms: int
    n_guarded_arms: int
    provenance: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return {
            "n_trials": self.n_trials,
            "n_usable_arms": self.n_usable_arms,
            "n_guarded_arms": self.n_guarded_arms,
            "dropout_marginal": round(self.dropout_marginal, 4),
            "dropout_marginal_kind": "guarded per-arm mean (started>=20 & rate<1.0)",
            "dropout_marginal_unguarded": round(self.dropout_marginal_unguarded, 4),
            "dropout_by_phase": {
                k: round(v, 4) for k, v in self.dropout_by_phase.items()
            },
            "dropout_by_arm_type": {
                k: round(v, 4) for k, v in self.dropout_by_arm_type.items()
            },
            "dropout_by_site_band": {
                k: round(v, 4) for k, v in self.dropout_by_site_band.items()
            },
            "dropout_by_duration_band": {
                k: round(v, 4) for k, v in self.dropout_by_duration_band.items()
            },
            "reason_mix": {k: round(v, 4) for k, v in self.reason_mix.items()},
            "early_fraction_assumed": self.early_fraction_assumed,
            "early_fraction_kind": "ASSUMPTION (AACT has no per-event timing)",
            "eligibility": self.eligibility,
            "covariate_provenance": self.covariate_provenance,
            "covariate_signs": self.covariate_signs,
            "covariate_sign_sources": {
                "age_years": "literature (younger T2D participants drop out more)",
                "hba1c_real": "literature + real (higher baseline burden -> more dropout)",
                "placebo_control_vs_active": "real T2D per-arm sign",
                "n_sites": "real T2D per-trial sign",
                "planned_duration_days": "real T2D per-trial sign",
            },
            "provenance": self.provenance,
        }


def duration_band(days: float) -> str:
    """Planned-duration band (quartile-ish T2D bins; documented, stable)."""
    if days <= 180:
        return "<=6mo"
    if days <= 365:
        return "6-12mo"
    if days <= 730:
        return "12-24mo"
    return ">24mo"


def site_band(n_sites: int) -> str:
    return "SINGLE_SITE" if int(n_sites) <= 1 else "MULTI_SITE"


def arm_type_band(arm_type: str) -> str:
    """Placebo-vs-active grouping (the task stratum). 'Placebo Comparator' -> control."""
    return "PLACEBO_CONTROL" if "placebo" in str(arm_type).lower() else "ACTIVE_OR_EXP"


def _read_ndjson(name: str, raw_dir: Path) -> pd.DataFrame:
    path = raw_dir / f"{name}.ndjson"
    if not path.exists():
        raise FileNotFoundError(
            f"Raw AACT file not found: {path}. The T2D cohort is selected from the pinned "
            f"build-time snapshot; it is never fetched at runtime."
        )
    return pd.read_json(path, lines=True)


def select_cohort(
    *,
    clean_root: Path = CLEAN_ROOT,
    raw_root: Path = RAW_ROOT,
    snapshot_date: str = SNAPSHOT_DATE,
) -> T2DCohort:
    """Reproduce the exact 755-trial / 2,402-usable-arm T2D cohort. Fail loud on drift."""
    raw_dir = raw_root / snapshot_date
    trial = pd.read_parquet(clean_root / "ref_trial.parquet")
    arm = pd.read_parquet(clean_root / "ref_arm.parquet")
    withdrawal = pd.read_parquet(clean_root / "ref_withdrawal_reason.parquet")

    industry = trial[
        (trial["sponsor_class"] == "INDUSTRY") & (trial["phase"].isin(MODELLING_PHASES))
    ]
    browse = _read_ndjson("browse_conditions", raw_dir)
    t2d_ncts = set(
        browse.loc[
            browse["mesh_term"].fillna("").str.contains(_T2D_MESH_PATTERN), "nct_id"
        ]
    )
    interventions = _read_ndjson("interventions", raw_dir)
    drugbio_ncts = set(
        interventions.loc[interventions["intervention_type"].isin(_DRUG_BIO), "nct_id"]
    )
    usable = arm[(arm["started"] > 0) & arm["completed"].notna()]

    cohort_ncts = (
        set(industry["nct_id"]) & t2d_ncts & drugbio_ncts & set(usable["nct_id"])
    )
    cohort_ncts = sorted(cohort_ncts)

    trials = trial[trial["nct_id"].isin(cohort_ncts)].reset_index(drop=True)
    arms = usable[usable["nct_id"].isin(cohort_ncts)].reset_index(drop=True)
    withdrawals = withdrawal[withdrawal["nct_id"].isin(cohort_ncts)].reset_index(
        drop=True
    )

    if len(trials) != EXPECTED_TRIALS or len(arms) != EXPECTED_USABLE_ARMS:
        raise ValueError(
            f"T2D cohort selection drifted: got {len(trials)} trials / {len(arms)} usable "
            f"arms, expected {EXPECTED_TRIALS} / {EXPECTED_USABLE_ARMS}. The selection "
            f"predicate or the snapshot changed — investigate before generating."
        )
    return T2DCohort(
        trials=trials, arms=arms, withdrawals=withdrawals, nct_ids=cohort_ncts
    )


def _guarded_arms(arms: pd.DataFrame) -> pd.DataFrame:
    """Drop label-suspect arms (started<20 OR dropout_rate==1.0) from dropout targets."""
    return arms[
        (arms["started"] >= MIN_STARTED) & (arms["dropout_rate"] < DROPOUT_RATE_CEILING)
    ]


def _dropout_by(
    guarded: pd.DataFrame, trials: pd.DataFrame, band_fn, trial_col: str | None
) -> dict[str, float]:
    """Mean guarded per-arm dropout grouped by a band derived from arm or trial column."""
    g = guarded.copy()
    if trial_col is not None:
        meta = trials.set_index("nct_id")[trial_col]
        g = g.assign(_band=g["nct_id"].map(meta).map(band_fn))
    else:
        g = g.assign(_band=g["arm_type"].map(band_fn))
    return {
        str(k): float(v) for k, v in g.groupby("_band")["dropout_rate"].mean().items()
    }


def _baseline_marginals(
    cohort: T2DCohort, raw_dir: Path
) -> tuple[dict[str, dict[str, Any]], dict[str, Any]]:
    """Real Table-1 covariate marginals (age, %female, HbA1c, BMI) + provenance/coverage."""
    cset = set(cohort.nct_ids)
    n = len(cset)
    bm = _read_ndjson("baseline_measurements", raw_dir)
    bm = bm[bm["nct_id"].isin(cset)].copy()
    bm["t"] = bm["title"].fillna("").str.lower().str.strip()
    bm["pvn"] = pd.to_numeric(bm["param_value_num"], errors="coerce")
    bm["cat"] = bm["category"].fillna("").str.lower()
    bm["cl"] = bm["classification"].fillna("").str.lower()
    cont = bm["param_type"].isin(["MEAN", "MEDIAN", "GEOMETRIC_MEAN"])

    def trial_mean(mask: pd.Series) -> pd.Series:
        return bm[mask & cont].groupby("nct_id")["pvn"].mean().dropna()

    age = trial_mean(bm["t"].str.contains("age") & bm["t"].str.contains("contin"))
    hba = trial_mean(
        bm["t"].str.contains("hba1c")
        | bm["t"].str.contains("glycosylated")
        | bm["t"].str.contains("hemoglobin a1c")
    )
    bmi = trial_mean(bm["t"].str.contains("body mass index") | (bm["t"] == "bmi"))

    # Sex %female: category/classification carries the Female/Male split (COUNT_OF_PARTICIPANTS).
    sex = bm[bm["t"].str.contains("sex") | bm["t"].str.contains("gender")].copy()
    sex["key"] = (sex["cat"] + " " + sex["cl"]).str.strip()
    fem = sex[sex["key"].str.contains("female")].groupby("nct_id")["pvn"].sum()
    mal = (
        sex[sex["key"].str.contains(r"(?:^|[^e])male", regex=True)]
        .groupby("nct_id")["pvn"]
        .sum()
    )
    sexdf = pd.concat([fem.rename("f"), mal.rename("m")], axis=1).fillna(0)
    sexdf = sexdf[(sexdf["f"] + sexdf["m"]) > 0]
    pf = sexdf["f"] / (sexdf["f"] + sexdf["m"])

    prov = {
        "age_years": CovariateProvenance(
            "age_years",
            "real_table1",
            age.size / n,
            float(age.mean()),
            float(age.std()),
            "Real Table-1 continuous age; missing trials use the real cohort marginal "
            "(age_baseline_imputed=True for those).",
        ).to_dict(),
        "pct_female": CovariateProvenance(
            "pct_female",
            "real_table1",
            pf.size / n,
            float(pf.mean()),
            float(pf.std()),
            "Real Table-1 sex split (category/classification counts).",
        ).to_dict(),
        "hba1c_pct": CovariateProvenance(
            "hba1c_pct",
            "real_table1",
            hba.size / n,
            float(hba.mean()),
            float(hba.std()),
            "Real where posted; elsewhere a LABELLED literature prior "
            f"(~{HBA1C_PRIOR_MEAN}%, hba1c_baseline_imputed=True).",
        ).to_dict(),
        "bmi": CovariateProvenance(
            "bmi",
            "real_table1",
            bmi.size / n,
            float(bmi.mean()),
            float(bmi.std()),
            "Real where posted; elsewhere a LABELLED literature prior "
            f"(~{BMI_PRIOR_MEAN} kg/m^2, bmi_baseline_imputed=True).",
        ).to_dict(),
        "hba1c_prior": CovariateProvenance(
            "hba1c_prior",
            "literature_prior",
            1.0 - hba.size / n,
            HBA1C_PRIOR_MEAN,
            HBA1C_PRIOR_SD,
            "Literature prior for trials with no posted HbA1c (T2D entry ~7.5-8.5%).",
        ).to_dict(),
        "bmi_prior": CovariateProvenance(
            "bmi_prior",
            "literature_prior",
            1.0 - bmi.size / n,
            BMI_PRIOR_MEAN,
            BMI_PRIOR_SD,
            "Literature prior for trials with no posted BMI (T2D ~30-32 kg/m^2).",
        ).to_dict(),
    }
    # Real per-trial values keyed by nct (used by the generator to seed real-where-posted draws).
    real_values = {
        "age": {k: float(v) for k, v in age.items()},
        "hba1c": {k: float(v) for k, v in hba.items()},
        "bmi": {k: float(v) for k, v in bmi.items()},
        "pct_female": {k: float(v) for k, v in pf.items()},
        "age_cohort_mean": float(age.mean()),
        "age_cohort_sd": float(age.std()),
    }
    return prov, real_values


def _reason_mix(withdrawals: pd.DataFrame) -> dict[str, float]:
    """Withdrawal-reason mix over the controlled vocab (censoring already excluded in clean)."""
    counts = withdrawals.groupby("reason")["count"].sum()
    total = counts.sum()
    if total <= 0:
        return {}
    return {str(k): float(v / total) for k, v in counts.items()}


def _eligibility(trials: pd.DataFrame, raw_dir: Path) -> dict[str, Any]:
    """Eligibility bounds (age min/max, sex) from clean + raw eligibilities."""
    elig = _read_ndjson("eligibilities", raw_dir)
    elig = elig[elig["nct_id"].isin(set(trials["nct_id"]))]
    return {
        "min_age_years": {
            "median": float(trials["min_age_years"].median()),
            "coverage_pct": float(trials["min_age_years"].notna().mean()),
        },
        "max_age_years": {
            "median": float(trials["max_age_years"].median()),
            "coverage_pct": float(trials["max_age_years"].notna().mean()),
        },
        "gender_mix": {
            str(k): int(v) for k, v in trials["gender"].value_counts().items()
        },
        "raw_gender_mix": {
            str(k): int(v) for k, v in elig["gender"].value_counts().items()
        },
    }


def _covariate_signs(cohort: T2DCohort) -> dict[str, int]:
    """Literature + real T2D dropout-association signs (the task STEP 3 sign check)."""
    guarded = _guarded_arms(cohort.arms).copy()
    meta = cohort.trials.set_index("nct_id")
    guarded["n_sites"] = guarded["nct_id"].map(meta["n_sites"])
    guarded["planned_duration_days"] = guarded["nct_id"].map(
        meta["planned_duration_days"]
    )
    guarded["is_placebo"] = (
        guarded["arm_type"].map(arm_type_band) == "PLACEBO_CONTROL"
    ).astype(float)

    def sign(col: str) -> int:
        x = guarded[col].to_numpy(float)
        y = guarded["dropout_rate"].to_numpy(float)
        ok = np.isfinite(x) & np.isfinite(y)
        if ok.sum() < 3 or np.std(x[ok]) == 0:
            return 0
        r = np.corrcoef(x[ok], y[ok])[0, 1]
        return int(np.sign(r)) if np.isfinite(r) else 0

    return {
        # Literature-fixed signs (carded; not derived from these aggregates):
        "age_years": -1,  # younger participants drop out more
        "hba1c_real": +1,  # higher baseline glycaemic burden -> more dropout
        # Real T2D per-arm/per-trial signs:
        "placebo_control_vs_active": sign("is_placebo"),
        "n_sites": sign("n_sites"),
        "planned_duration_days": sign("planned_duration_days"),
    }


def compute_targets(
    cohort: T2DCohort,
    *,
    raw_root: Path = RAW_ROOT,
    snapshot_date: str = SNAPSHOT_DATE,
) -> tuple[T2DTargets, dict[str, Any]]:
    """Assemble the real (+ labelled-prior) T2D calibration targets and the real-values map.

    Returns ``(targets, real_values)`` — ``real_values`` carries the per-trial real Table-1
    baseline values the generator seeds real-where-posted draws from (not part of the public
    target artifact, but used internally for the joint sample).
    """
    raw_dir = raw_root / snapshot_date
    guarded = _guarded_arms(cohort.arms)
    prov, real_values = _baseline_marginals(cohort, raw_dir)

    targets = T2DTargets(
        dropout_marginal=float(guarded["dropout_rate"].mean()),
        dropout_marginal_unguarded=float(cohort.arms["dropout_rate"].mean()),
        dropout_by_phase=_dropout_by(guarded, cohort.trials, lambda p: p, "phase"),
        dropout_by_arm_type=_dropout_by(guarded, cohort.trials, arm_type_band, None),
        dropout_by_site_band=_dropout_by(guarded, cohort.trials, site_band, "n_sites"),
        dropout_by_duration_band=_dropout_by(
            guarded, cohort.trials, duration_band, "planned_duration_days"
        ),
        reason_mix=_reason_mix(cohort.withdrawals),
        early_fraction_assumed=EARLY_FRACTION_ASSUMED,
        eligibility=_eligibility(cohort.trials, raw_dir),
        covariate_provenance=prov,
        covariate_signs=_covariate_signs(cohort),
        n_trials=len(cohort.trials),
        n_usable_arms=len(cohort.arms),
        n_guarded_arms=len(guarded),
        provenance={
            "snapshot_date": snapshot_date,
            "aact_source": "ClinicalTrials.gov / AACT (build-time snapshot)",
            "cohort": "T2D INDUSTRY drug/biologic, modelling phases, >=1 usable arm",
            "guard": f"dropout targets exclude started<{MIN_STARTED} OR dropout_rate==1.0",
            "literature_priors": {
                "hba1c": f"~{HBA1C_PRIOR_MEAN}% (7.5-8.5% band)",
                "bmi": f"~{BMI_PRIOR_MEAN} kg/m^2 (30-32 band)",
            },
            "note": "Real targets from AACT/ref_*; HbA1c/BMI priors + early_fraction are "
            "LABELLED assumptions, never claimed as real.",
        },
    )
    return targets, real_values


def write_targets(targets: T2DTargets, out_root: Path) -> Path:
    out_root.mkdir(parents=True, exist_ok=True)
    path = out_root / "calibration_targets.json"
    path.write_text(
        json.dumps(targets.to_dict(), indent=2, sort_keys=True), encoding="utf-8"
    )
    return path


T2D_SYNTHETIC_ROOT = DATA_ROOT / "synthetic" / "t2d"
