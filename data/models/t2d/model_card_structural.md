# Model Card: Vigil T2D Structural GBT — Shadow Model

## Role

**SHADOW** — this model runs alongside the sequence LSTM champion for monitoring and
comparison. Its scores are stored in `participant_score` (discriminated by `model_version`)
but are NEVER surfaced to clinical reads (`GET /cohort`, `GET /participants/*/risk`).
Champion-only surfacing is enforced at the application layer (denorm-cache guard + model_version
filter). See `specs/routing.md § Champion/challenger/shadow`.

## Data Source

**DATA SOURCE: REAL AACT T2D registry (arm-level aggregates).**
755 INDUSTRY T2D trials / 2,111 guarded real arms (started ≥ 20, dropout_rate < 1.0).
ClinicalTrials.gov snapshot 2026-06-05. No per-participant data. No PHI.

This model is trained on arm-level structural features (phase, arm_type, enrollment,
n_sites, planned_duration_days, etc.) from the real registry. It is the real structural
floor for T2D, NOT a trajectory/sequence model.

## Model Type

`HistGradientBoostingRegressor` (NaN-native GBT, no imputation, no rebalancing).
Predicts arm-level dropout_rate; thresholded at train-median to produce a binary dropout
indicator for PR-AUC evaluation.

Train 1995-11-30..2012-12-31 / val 2013-01-11..2016-08-15 / test 2016-09-20..2023-09-22
(temporal group-disjoint split by trial start_date).

## Metrics

- test GBT PR-AUC: 0.3425 (n=415 test arms, positives=117)
- test GBT MAE: 0.1206
- train-median threshold: 0.1231

## Features

Arm-level aggregates from the real registry: phase, therapeutic_area, sponsor_class,
allocation, intervention_model, masking, primary_purpose, gender, arm_type, enrollment,
n_arms, n_sites, n_countries, planned_duration_days, min_age_years, max_age_years,
started, healthy_volunteers.

When scoring live demo participants, only arm_type, phase, n_sites, and
planned_duration_days are available from the operational DB; remaining features are
NaN (handled natively by HistGradientBoostingRegressor + _missing indicators).

## LIMITATIONS

- ARM-LEVEL model: trained on per-arm aggregates, NOT per-participant trajectories.
  Individual demo participant scores use mostly-missing structural features; scores
  are structurally valid but carry high uncertainty.
- This is the REAL structural floor (~0.34 PR-AUC) — context for the sequence model's
  +0.088 trajectory lift. NOT a standalone predictor.
- Does NOT consume visit trajectory (missed visits, consecutive_missed, etc.).
- NO clinical prediction validity: arm-level aggregate model from registry data only.
- SHADOW role: visible only to platform roles querying participant_score directly.
  Not surfaced on dashboard or participant risk panels.
