# Phase 3 Modelling — Scorecard
> Data honest as of: 2026-06-09 | Synthetic cohort v2 (censored) | No PHI

---

## Section 1: BREADTH — Pan-indication structural signal (REAL data/clean)

**Pan-indication GBT TEST PR-AUC = 0.697** (REAL `ref_trial` + `ref_arm`, 37k+ arms, all
modelling phases, temporal split). Source: `data/models/baselines/metrics.json`.

### Per-indication decomposition

| Indication | n_arms | Within-indication PR-AUC | Split |
|---|---|---|---|
| T2D | 2,666 | 0.3809 | temporal |
| PSO | 1,407 | 0.6474 | temporal |
| IBD | 1,207 | 0.4975 | temporal |
| ALZ | 927 | 0.7747 | temporal |
| MDD | 791 | 0.3206 | temporal |
| MS | 719 | 0.6201 | temporal |
| HF | 548 | 0.5829 | temporal |
| RA | 9 | N/A | small-N random |

Source: `data/models/decomposition/indication_pr_auc.json`.

**Key finding:** The pooled 0.697 is substantially inflated by base-rate variation between
indications. T2D (~14% dropout base rate) mixes with ALZ, PSO, and others that have different
structural characteristics. Within-indication scores tell the true story: T2D (0.38) and MDD
(0.32) sit at the low end; ALZ (0.77) and PSO (0.65) at the high end.

Structural covariates predict BETWEEN-indication dropout more than WITHIN-indication participant risk.

---

## Section 2: WHY T2D

T2D is the largest-cohort indication in the decomposition (2,666 arms — 3× the next largest in
the breakdown), AND it has the weakest within-indication structural signal (0.38 vs the pooled
0.70). That combination — maximum data, minimum covariate signal — makes it the correct and
hardest test for whether adding visit-trajectory features produces a real lift.

ALZ (within-indication 0.77) already captures substantial dropout structure from covariates
alone. Trajectory may not be the discriminative gap there. T2D is the stress test.

---

## Section 3: DEPTH — T2D trajectory modelling (SYNTHETIC cohort)

### 3a. Provenance ladder

| Layer | Source | Notes |
|---|---|---|
| Trial-level outcomes | REAL AACT (755 trials, 2,402 arms guarded) | `arm_real_dropout_rate` from reported results |
| Age, sex | REAL where posted (92.2% / 100%) | From baseline_measurements re-extract; `age_baseline_imputed=True` for ~8% |
| HbA1c | REAL 44.5%, literature prior 55.5% | N(~8.0%, SD 0.6) for imputed; `hba1c_baseline_imputed=True` |
| BMI | REAL 20.5%, literature prior 79.5% | N(~31.0, SD 3.0) for imputed; `bmi_baseline_imputed=True` |
| Visit trajectory | SYNTHETIC FULLY | CAMP-style hazard; planted assumption ≥3 consecutive missed visits OR threshold |
| `miss_probability` | LATENT generator variable | NEVER a feature — would trivially recover the planted rule |

Non-separability AUC on engagement features = **0.767** (guard: asserted ≤ 0.85).
Source: `data/synthetic/t2d/calibration_report_v2.json`.

Coverage percentages sourced from the `covariate_provenance` block of the calibration report.
Imputed flag prevalences: age 9.2%, HbA1c 55.4%, BMI 79.6%.

### 3b. Model progression (T2D synthetic cohort)

| Model | Input | TEST metric | Value | vs prior |
|---|---|---|---|---|
| 1a Real structural floor (GBT) | REAL T2D covariates only | PR-AUC | 0.3425 | real floor |
| 1b Structural-only GBT (synthetic) | Covariates only, no trajectory | PR-AUC | 0.2506 | bar |
| 3 Sequence LSTM | Covariates + full visit trajectory | PR-AUC | 0.339 | **+0.088 ← the one real lift** |
| (c) Discrete-time hazard | Covariates + trajectory (panel) | C-index | 0.400 | — |

Pre-registered bar: sequence TEST PR-AUC ≥ 0.3006 — **MET** (0.339 > 0.3006).
Source: `data/models/t2d/preregistration.json` (written before sequence fit, timestamped 2026-06-08).

### 3c. Survival model — NEGATIVE result

C-index 0.400 ≈ chance. For concordance index: 0.5 = random ranking; 0.4 is slightly
anti-concordant — the model, when applied to time-to-event ranking, performs marginally worse
than random ordering among dropouts.

The discrete-time hazard correctly separates event vs non-event at the extreme hazard quartiles
(calibration Q4 observed 19.8% vs Q1 observed 18.0%), but **does NOT rank time-to-event** — i.e.,
among participants who drop out, the model cannot reliably identify who drops out sooner.

This is NOT a calibration win dressed up as survival analysis.

**Stated plainly:** Adding TTE-ranking (C-index) over event-detection (PR-AUC) produced no
discrimination gain on this cohort.

**Why (honest):** On a SYNTHETIC cohort where the planted rule is ≥3 consecutive missed visits
(a discrete threshold), the timing of dropout is structurally harder to rank than the event
itself. The hazard accumulated monotonically; late-dropout participants and early-dropout
participants accumulate hazard at similar rates until the threshold is crossed. Real IPD with
richer pre-dropout signals (ePRO, unscheduled contacts, partial compliance) would test this
properly.

Censoring: ~0.03% administrative cutoff (39 participants / 121,225 total), non-informative
(max |r| = 0.099 with covariates, threshold 0.15). Censoring is not the driver of this result.

Source: `data/models/t2d/survival_metrics.json`.

### 3d. Lead-time (authoritative: hazard-curve sweep, not single threshold)

The sequence model at threshold 0.5 flagged 9.0% of test droppers (326 / 3,634) with a
17-visit median lead-time. That 17-visit figure applies only to the 9% who crossed the
threshold — it is not representative of operational performance.

The hazard-curve sweep over operating points is the authoritative lead-time picture:

| t_flag (visit) | Flagged fraction of droppers | Median lead-time (visits) |
|---|---|---|
| 1 | 0.14% | 21 |
| 2 | 0.21% | 18 |
| 3 | 0.35% | 14.5 |
| 5 | 0.61% | 10 |
| 8 | 2.0% | 7 |
| 13 | 8.9% | 5 |
| 21 | 30.5% | 5 |

At t_flag=21: 30.5% of droppers flagged, **5-visit median lead-time**. This is the realistic
operating point — it sweeps the coverage/lead-time tradeoff honestly rather than locking to
one classification threshold.

Source: `data/models/t2d/survival_metrics.json` → `lead_time_curve`.

---

## Section 4: CENTRAL THESIS

Structural covariates (arm-level registry features) predict between-indication dropout
variation — pooled 0.697 — but mostly capture indication base rates, not within-trial
participant risk.

Within-indication, structural signal is weak (T2D 0.38, MDD 0.32); trajectory features (visit
attendance pattern) add +0.088 PR-AUC on the T2D synthetic cohort — the one real discriminative
lift produced in Phase 3.

Survival (TTE-ranking) added no gain over event-detection: C-index 0.40 is a negative result,
stated plainly.

Validation of the trajectory hypothesis requires real longitudinal individual-participant data
(IPD) — not available. The synthetic cohort proves the architecture and pipeline; it does not
clinically validate the precursor hypothesis.

BMI ~80% / HbA1c ~55% are literature-prior imputed — these covariates are placeholders that
match real cohort marginals by design but are not real patient measurements.

---

## Section 5: WHAT THIS DOES NOT PROVE

- The 0.697 pan-indication PR-AUC is NOT a clinical prediction accuracy; it largely reflects
  that the model learned which indication has the higher base dropout rate.
- The +0.088 sequence lift is NOT validated on real IPD — the trajectory is fully synthetic,
  generated by a planted CAMP-style hazard rule.
- The survival C-index 0.40 is NOT a clinical survival model — it is a negative result on a
  synthetic cohort where the planted rule is a discrete visit-count threshold.
- BMI / HbA1c literature-prior values are NOT real patient measurements; they are labelled
  imputations calibrated to published T2D trial population statistics.
- No PHI was used at any stage.

---

## Section 6: NEXT (from ROADMAP, pending items only)

- Phase 4: FastAPI endpoint wiring — wire each stub boundary to the scoped data layer
  (see ROADMAP frontend wiring register)
- Phase 4: Model routing — regime routing, champion/challenger shadow, drift-triggered
  fallback, audited promotion
- `check_specs.py` enforcement for held-out split — extend conformance check so a models
  phase must declare a held-out split (tracked in ROADMAP housekeeping TODOs)
- Scoring writeback — `risk_score` → Postgres behind RLS; operational integration of the
  sequence model output
- Frontend lint/typecheck in CI — `tsc --noEmit` once the API lands (currently skipped,
  `ignoreBuildErrors: true` in `next.config.mjs`)
