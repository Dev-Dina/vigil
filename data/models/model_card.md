# Model Card: Vigil Phase-3 Retention Baselines (GBT regressor + Logistic classifier)

## Data Source

**DATA SOURCE: REAL ClinicalTrials.gov/AACT cleaned ref_* (modelling cohort 37246 trials / 96679 arms across train+val+test). NOT synthetic.**

- Model type: HistGradientBoostingRegressor on continuous dropout_rate (NaN-native, no imputation) + LogisticRegression on a train-median-thresholded label (missing-indicator method)
- Cohort: Modelling phases ['PHASE1/PHASE2', 'PHASE2', 'PHASE2/PHASE3', 'PHASE3']; temporal group-disjoint-by-nct_id split — train 1966-07-31..2015-02-19, val 2015-02-20..2018-07-18, test 2018-07-19..2025-06-24.
- Target: continuous per-arm dropout_rate (regression); thresholded at the train-median (0.1129) for PR-AUC framing

## Features

- categorical: phase
- categorical: therapeutic_area
- categorical: sponsor_class
- categorical: allocation
- categorical: intervention_model
- categorical: masking
- categorical: primary_purpose
- categorical: gender
- categorical: arm_type
- numeric: enrollment
- numeric: n_arms
- numeric: n_sites
- numeric: n_countries
- numeric: planned_duration_days
- numeric: min_age_years
- numeric: max_age_years
- numeric: started
- boolean: healthy_volunteers
- min_age_years / max_age_years carry explicit _missing indicators (never imputed)

## Metrics

- gbt_brier: 0.2648
- gbt_mae: 0.204
- gbt_pr_auc: 0.6968
- gbt_r2: 0.3222
- gbt_rmse: 0.2818
- lead_time_gain: N/A
- logit_brier: 0.2244
- logit_pr_auc: 0.6538
- logit_recall_at_p50: 0.9325

### Per-Sponsor Class Metrics

| sponsor_class | gbt_brier | gbt_mae | gbt_pr_auc | gbt_r2 | gbt_recall_at_p50 | gbt_rmse | logit_brier | logit_pr_auc | logit_recall_at_p50 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| ACADEMIC_OTHER | 0.2578 | 0.1857 | 0.4491 | 0.1117 | 0.05837 | 0.2609 | 0.2289 | 0.4344 | 0.1107 |
| INDUSTRY | 0.2679 | 0.2103 | 0.7414 | 0.3498 | 1 | 0.2883 | 0.222 | 0.6879 | 1 |
| NIH | 0.2494 | 0.2042 | 0.522 | 0.1726 | 0.5607 | 0.2876 | 0.2415 | 0.4482 | 0.3393 |

## Calibration

GBT predicted rate clipped to [0,1] then compared to the high-dropout (>train median 0.1129) label; Brier (GBT) = 0.2648, Brier (logistic) = 0.2244. See calibration_*.png.

## LIMITATIONS

- Sponsor-level leakage is an ACCEPTED limitation: the held-out split is group-disjoint by nct_id, NOT by sponsor, so one sponsor's trials may span folds (sponsor identity is never pulled as a feature).
- Aggregate per-ARM dropout rates, not per-participant sequences: this is a registry baseline proving METHOD validity, never a clinical prediction.
- The temporal TEST window is recent and narrow versus the long train history.
- The linear model uses the missing-indicator method (zero-fill of standardized NaN paired with explicit _missing indicators); the GBT consumes NaN natively with NO imputation.
- No rebalancing is applied to either model (per the Evaluation contract).
- Lead-time gain is N/A for this aggregate baseline (no per-participant time series).
