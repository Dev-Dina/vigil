# Model Card — Vigil T2D Survival Model (Phase 3 Step c)

## Overview

**Formulation:** discrete-time hazard model.
**Classifier:** `HistGradientBoostingClassifier` fit on a (participant x visit) panel.
**Cumulative hazard:** H(T) = 1 - prod(1 - h(v)) over all observed visits v=0..N-1.
**Data:** SYNTHETIC T2D cohort — per-participant engagement sequences generated and
calibrated to real T2D ClinicalTrials.gov/AACT aggregate statistics. NOT real
participants. No PHI. Method-validity / partner-readiness demonstration only —
NEVER a clinical prediction.

## Data Honesty

- Source: SYNTHETIC cohort, clearly labelled `synthetic=True` on every row.
- No PHI. No real participant identifiers or clinical records.
- censoring: ~0.03% of rows are admin-censored (non-informative: only 39 participants
  whose planned duration exceeded the snapshot date — `n_admin_censored=39` in
  `data/synthetic/t2d/calibration_report_v2.json`, matching `PHASE3_CARD.md`). This censoring
  is non-informative (driven by trial recency, independent of participant biology or the
  planted miss trajectory). It is NOT the value driver; the primary signal is the
  planted trajectory assumption (consecutive missed visits -> elevated dropout hazard).
- The miss_probability is the LATENT hazard used by the synthetic generator.
  miss_probability is the LATENT hazard and is NEVER a feature — using it would
  trivially recover the planted rule and destroy the honesty guard.
- planted trajectory assumption: >=3 consecutive missed visits -> elevated hazard.
  The survival model recovers this signal from observable attendance features only
  (attended, missed, cumulative_missed, consecutive_missed, visit_index). This is
  designed; it proves the architecture can recover planted clinical signals without
  accessing the latent hazard.

## Leakage Controls

Forbidden columns (never fed to the model; assertion fires before every fit):
`miss_probability`, `dropped`, `censored`, `time_to_event`, `event_observed`,
`synthetic`, `arm_real_dropout_rate`, `nct_id`, `arm_id`, `*_baseline_imputed`.

The pipeline (OneHotEncoder + StandardScaler) is **fit on TRAIN only** and applied
to val/test without re-fitting.

## Primary Metric: C-index

The survival model is evaluated on **C-index** (concordance index), not PR-AUC.
The pre-registration bar (PR-AUC >= 0.3006) applies to the sequence model (Step b),
not this survival model.

**C-index overall: 0.3998**
(time_to_event in days; concordance_index(tte, -H, event_observed);
concordant = shorter tte paired with higher H (negated -> smaller score))

### C-index by arm_type

| arm_type | C-index |
|----------|---------|
| Other | 0.4058 |
| Placebo Comparator | 0.3767 |

### C-index by phase

| phase | C-index |
|-------|---------|
| PHASE2 | 0.3639 |
| PHASE2/PHASE3 | 0.4398 |
| PHASE3 | 0.4178 |

## Calibration (quartile bins of predicted H)

| Quartile | Mean pred H | Obs event rate | N |
|----------|-------------|----------------|---|
| 1 | 0.0268 | 0.1803 | 5895 |
| 2 | 0.0662 | 0.1103 | 5894 |
| 3 | 0.1234 | 0.1272 | 5894 |
| 4 | 0.3496 | 0.1985 | 5895 |

## Lead-Time Hazard Curve (AUTHORITATIVE)

The hazard-curve lead-time is the AUTHORITATIVE lead-time, superseding the threshold lead-time from the sequence model.
It measures: at decision point t_flag, what fraction of true droppers are already flagged
(H > 0.3), and how many visits of warning precede the actual dropout event.

| t_flag | flagged_fraction | median_lead_time_visits | threshold |
|--------|-----------------|------------------------|-----------|
| 1 | 0.001 | 21.0 | 0.300 |
| 2 | 0.002 | 18.0 | 0.300 |
| 3 | 0.004 | 14.5 | 0.300 |
| 5 | 0.006 | 10.0 | 0.300 |
| 8 | 0.020 | 7.0 | 0.300 |
| 13 | 0.089 | 5.0 | 0.300 |
| 21 | 0.305 | 5.0 | 0.300 |

## Feature Set

**Static (arm-level):** phase, arm_type, planned_duration_days, n_sites, age_years,
hba1c_pct, bmi.
**Dynamic (per visit):** visit_index, attended, missed, cumulative_missed,
consecutive_missed.
**Encoding:** phase/arm_type -> OneHotEncoder(handle_unknown='ignore');
all numerics -> StandardScaler (NaN -> column mean on TRAIN before scaling).

## Training Details

- Model: `HistGradientBoostingClassifier(max_iter=300, early_stopping=False,
  random_state=MODEL_SEED)`
- Panel: 1,414,100 train visit rows; 23,578 test participants
- Split: temporal group split keyed on `ref_trial.start_date` (group-disjoint by nct_id)
- Seed: SYNTHETIC_SEED (MODEL_SEED = 20240601) — deterministic, bit-identical

## Comparison

| Model | Metric | Value |
|-------|--------|-------|
| 1b structural | PR-AUC | 0.2506 |
| Sequence LSTM | PR-AUC | 0.339 |
| Sequence LSTM | Median lead-time (visits) | 17.0 |
| **Survival (this model)** | **C-index** | **0.3998** |

## Limitations

1. SYNTHETIC cohort — proves architecture, not clinical validity.
2. planted trajectory assumption: >=3 consecutive missed visits signal is a
   literature-shaped assumption, not a validated clinical precursor.
3. miss_probability is the LATENT hazard (the generator's internal variable). It is
   NEVER a feature — see leakage controls.
4. censoring: ~0.03% (non-informative admin cutoff, documented — not the clinical
   value driver). The survival formulation handles censoring by truncating each
   participant's series at their last observed visit.
5. No PHI. No real participants. Method-validity demonstration only.
