# Model Card: Vigil Phase-3 T2D Sequence Model (synthetic-cohort LSTM) + structural anchors

## Data Source

**DATA SOURCE: SYNTHETIC T2D cohort — per-participant sequences GENERATED and calibrated to real T2D ClinicalTrials.gov/AACT aggregate statistics plus labelled literature priors. NOT real participants. NO PHI. Method-validity / partner-readiness only, NEVER a clinical prediction. The 1a anchor is the REAL T2D registry floor (aggregate per-arm).**

- Model type: torch nn.LSTM per-visit dropout-risk classifier (static covariates seed the initial hidden state) on the synthetic cohort; structural anchors are HistGradientBoosting + LogisticRegression on real (1a) and synthetic-structural (1b).
- Cohort: 755 T2D INDUSTRY modelling-cohort trials / 2111 guarded real arms (started>=20, dropout_rate<1.0); 20000 synthetic train participants (documented subsample), full test fold. Held-out axis: temporal, trial-level, keyed on ref_trial.start_date, group-disjoint by nct_id (earlier-starting trials -> train, later -> test). Within-participant visit-time is the sequence the LSTM consumes; lead-time is measured in visits before the dropout event. Train 1995-11-30..2012-12-31, val 2013-01-11..2016-08-15, test 2016-09-20..2023-09-22.
- Target: per-visit decision-time dropout label: at t the model sees visits<t and predicts whether the participant drops at a LATER visit; right-censored decision points are masked. 1a target = continuous per-arm dropout_rate; 1b = terminal participant dropped (censored==observed completer==negative, no early right-censoring).

## Features

- sequence (per visit, observable ONLY): attended, missed, cumulative_missed, consecutive_missed, visit_index
- static context: age_years, sex, hba1c_pct, bmi, phase, arm_type, n_sites, planned_duration_days
- EXCLUDED as features (fail-loud): miss_probability (LATENT generator hazard), synthetic, *_baseline_imputed provenance, dropped/censored/arm_real_dropout_rate/dropout_visit_index/dropout_reason (outcome), ids

## Metrics

- 1a_real_floor_gbt_mae: 0.1206
- 1a_real_floor_gbt_pr_auc: 0.3425
- 1a_real_floor_logit_pr_auc: 0.3375
- 1b_synthetic_structural_brier: 0.1258
- 1b_synthetic_structural_pr_auc: 0.2506
- 1b_synthetic_structural_recall_at_p50: 0
- bar_lead_time_PASS: True
- bar_overall_PASS: True
- bar_pr_auc_PASS: True
- preregistered_bar_median_lead_time: 2
- preregistered_bar_pr_auc_must_reach: 0.3006
- sequence_brier: 0.07526
- sequence_median_lead_time_visits: 17
- sequence_pr_auc: 0.339
- sequence_recall_at_p50: 0.1891

### Per-Sponsor Class Metrics

| sponsor_class | brier | n | positives | pr_auc | recall_at_p50 |
| --- | --- | --- | --- | --- | --- |
| arm_type:Other | 0.07144 | 358731 | 32058 | 0.3136 | 0.1512 |
| arm_type:Placebo Comparator | 0.08958 | 95541 | 11606 | 0.404 | 0.2947 |
| phase:PHASE2 | 0.068 | 105883 | 8445 | 0.2243 | 0.01978 |
| phase:PHASE2/PHASE3 | 0.0714 | 8309 | 684 | 0.2097 | 0.04678 |
| phase:PHASE3 | 0.07761 | 340080 | 34535 | 0.366 | 0.2351 |

## Calibration

Sequence Brier (test, decision points) = 0.0753; 1b structural Brier = 0.1258; 1a real GBT Brier = 0.2169. See calib_*.png. The planted trajectory->dropout relationship is noisy + non-separable (AUC~0.77 by construction), so neither model is expected to be near-perfect.

## LIMITATIONS

- SYNTHETIC cohort: generated per-participant sequences calibrated to real T2D AACT aggregates + literature priors — NOT real participants, NO PHI, method-validity only.
- The trajectory->dropout relationship is a PLANTED, literature-shaped assumption (>=3 consecutive missed visits / hazard threshold, noisy). The sequence model beating 1b demonstrates the ARCHITECTURE and the incremental value of trajectory features ON THIS COHORT — it does NOT validate the dropout-precursor hypothesis on real data.
- BMI is ~80% literature-prior (imputed); HbA1c ~55% imputed; imputed covariates carry NO planted signal (provenance flags are never features).
- miss_probability (the generator's LATENT hazard) is NEVER a feature — using it would trivially recover the planted rule and destroy the honesty guard.
- Sponsor-level leakage is an ACCEPTED limitation: the split is group-disjoint by nct_id, not by sponsor (sponsor identity is never a feature).
- No early right-censoring exists in this cohort (censored==observed completer); the per-visit mask still enforces decision-time censoring for the sequence labels.
- No rebalancing anywhere (per the Evaluation contract); scalers/encoders fit on TRAIN only; the forbidden-feature assertion fires before every fit.
