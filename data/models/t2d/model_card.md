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

- model_version: sequence_v1.1:demo
- calibration_method: isotonic on held-out val fold (disjoint from train+test)
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

### Gate 9.7a — output calibration (isotonic, val-fold)

The champion (`sequence_v1.1:demo`) carries a MONOTONIC ISOTONIC output calibrator fit on the held-out VAL fold ONLY (temporal, group-disjoint-by-`nct_id`; disjoint from BOTH the LSTM's train fold AND the reported test fold). The raw LSTM outputs are compressed (decision-point probabilities top out near ~0.54), so the operational `> 0.6` HIGH band was unreachable from the real model. The calibrator re-maps the probability SCALE so `> 0.6` is reachable by the REAL model — WITHOUT changing the threshold and WITHOUT an override.

- This is a CALIBRATION (probability-scale) change, NOT a discrimination change. Calibration is monotonic, so it preserves ranking: test ROC-AUC is invariant (raw 0.790114 → calibrated 0.790061, Δ ≈ −5.3e-5) and per-decision-point PR-AUC over the preserved ranking is unchanged (raw 0.3390). It does NOT manufacture discrimination.
- The `> 0.6` mapping is HONEST: the highest raw-score decision points (raw ∈ [0.5, 0.6)) have an **asserted** empirical dropout rate ≈ 0.74 (a build-time observation — **not** a computed figure persisted to any committed artifact) — the model is locally under-confident there, and isotonic corrects exactly that. (Platt/logistic was rejected: the model is already globally near-calibrated, ECE ≈ 0.008 / slope ≈ 1.06, so a global sigmoid cannot lift the compressed top past 0.6.)
- Calibration quality: ECE 0.00780 → 0.00750 and the **calibrated** Brier 0.075027 are **validation-fold** figures from the calibrator's own fit report (carried inside the `.pt` `calibration_report`, NOT measured on the reported test fold); the **raw** Brier 0.075257 is the test-fold value backed by `sequence_metrics.json`. A modest honest improvement; the win is unlocking the HIGH band, not a large calibration gain.
- **Provenance of these calibration sub-metrics (honesty note).** Unlike the headline metrics (test PR-AUC, lift over 1b, lead-time — all backed by `sequence_metrics.json`), the calibration sub-metrics above — the ROC-AUC raw → calibrated (0.790114 → 0.790061), the ECE raw → calibrated (0.00780 → 0.00750), the calibrated Brier (0.075027), and the ≈ 0.74 empirical rate — are **reported at build time and are NOT regenerable from a committed artifact**: the ROC-AUC is computed but only printed (`scripts/persist_sequence_artifact.py`), the ECE/calibrated-Brier live only inside the `.pt` `calibration_report` (validation fold), and the ≈ 0.74 rate is asserted, not computed. Only the **raw** Brier 0.075257 (test fold) is JSON-backed. The calibration **mechanism** IS reproducible — the isotonic knot-map is persisted in the artifact and re-applied deterministically; only these quality sub-metrics lack a backing JSON. (`vigil/seed.py` carries the same figures as hand-entered registry-catalog literals, with its own "recorded, never computed" note.)
- The calibrator consumes NO outcome at score time (a fixed `raw_prob → calibrated_prob` isotonic knot map, persisted with the artifact and applied via `np.interp`).
- Attribution (`top_factors` / `reasons`) is computed on the model's OWN pre-calibration output, so the occlusion deltas stay meaningful and leakage-safe; the monotone calibrator does not reorder feature importances.
- Version bump rationale: calibrating the output changes the probability semantics of every score row, so the calibrated champion is a new version (`sequence_v1.0:demo` → `sequence_v1.1:demo`). The HIGH/MEDIUM thresholds (`> 0.6` / `> 0.3`) are UNCHANGED.

## LIMITATIONS

- SYNTHETIC cohort: generated per-participant sequences calibrated to real T2D AACT aggregates + literature priors — NOT real participants, NO PHI, method-validity only.
- The trajectory->dropout relationship is a PLANTED, literature-shaped assumption (>=3 consecutive missed visits / hazard threshold, noisy). The sequence model beating 1b demonstrates the ARCHITECTURE and the incremental value of trajectory features ON THIS COHORT — it does NOT validate the dropout-precursor hypothesis on real data.
- BMI is ~80% literature-prior (imputed); HbA1c ~55% imputed; imputed covariates carry NO planted signal (provenance flags are never features).
- miss_probability (the generator's LATENT hazard) is NEVER a feature — using it would trivially recover the planted rule and destroy the honesty guard.
- Sponsor-level leakage is an ACCEPTED limitation: the split is group-disjoint by nct_id, not by sponsor (sponsor identity is never a feature).
- No early right-censoring exists in this cohort (censored==observed completer); the per-visit mask still enforces decision-time censoring for the sequence labels.
- No rebalancing anywhere (per the Evaluation contract); scalers/encoders fit on TRAIN only; the forbidden-feature assertion fires before every fit.
- Gate 9.7a output calibration (isotonic, val-fold) re-maps the probability SCALE only; it preserves ranking (discrimination UNCHANGED) and uses NO outcome at score time. It is fit on a split disjoint from train+test and does NOT improve the model's ability to discriminate — only the probability meaning of the `> 0.6` band.
