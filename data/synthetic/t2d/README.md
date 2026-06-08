# T2D synthetic cohort

SYNTHETIC — calibrated to real T2D AACT aggregates + labelled literature priors; NOT real participants; method-validity only; no PHI.

This is a build-time, method-validity artifact: a per-participant Type-2-diabetes cohort whose
aggregates reproduce the REAL T2D AACT statistics, with per-visit engagement trajectories for a
sequence model to learn from. It contains NO real participants and NO PHI.

## Cohort
- Selection: INDUSTRY lead, drug/biologic, modelling phases {PHASE1/PHASE2, PHASE2,
  PHASE2/PHASE3, PHASE3}, T2D MeSH ("Diabetes Mellitus, Type 2"), >=1 usable arm
  (started>0 & completed not null). Reproduces EXACTLY 755 trials /
  2402 usable arms (fails loud on drift).
- Synthetic cohort size: 121225 participants,
  2285102 per-visit engagement rows. Per arm we synthesise
  min(60, started) participants (>= 8); the
  real summed `started` (~580k) exceeds the documented cap, so each arm is capped at
  60.
- Dropout target guard: per-arm dropout marginals exclude arms with started<20 OR
  dropout_rate==1.0 (all-lost arms are label-suspect). Guarded marginal =
  0.1438.

## Determinism
Seed: 20240601 (`from ingestion import SYNTHETIC_SEED`). All randomness derives from a
single numpy.random.Generator(seed); regeneration is bit-identical. Every participant row and
every engagement row carries `synthetic = True`.

## Provenance layers
- Layer 3 (baseline covariates): per participant, sampled from the calibrated joint, HARD-
  BOUNDED by the trial's eligibility ranges (min/max age; sex per real %female). Imputed
  HbA1c / BMI are sampled as MARGINALS ONLY (no invented correlation) and are EXCLUDED from the
  hazard. Real-where-posted HbA1c may enter the hazard.
- Layer 4 (per-visit engagement): over the trial's derived visit schedule (one visit per
  ~28 days of planned duration; documented default when
  absent). Per-visit miss probability is a hazard over REAL covariates, arm, cumulative missed
  visits and trial burden. Dropout fires probabilistically with a CAMP-style consecutive-miss
  boost (3+ consecutive misses raise — not force — the
  hazard). Observations stop at the dropout visit (no post-dropout rows), so no future/outcome
  data leaks.

## Per-covariate provenance + coverage
| covariate | source | coverage | mean | imputed flag |
|---|---|---|---|---|
| age | real_table1 | 92.2% | 56.7 y | `age_baseline_imputed` |
| sex (%female) | real_table1 | 100.0% | 43.9% | — |
| HbA1c | real_table1 | 44.5% | 8.17% | `hba1c_baseline_imputed` |
| BMI | real_table1 | 20.5% | 31.0 | `bmi_baseline_imputed` |

Imputed values use LABELLED literature priors: HbA1c ~8.0% (7.5-8.5% band),
BMI ~31.0 kg/m^2 (30-32 band). The `*_imputed` flags mark which participants carry a
prior-sampled value; imputed HbA1c/BMI never drive dropout.

## Stated assumption: trajectory -> dropout
The deteriorating-engagement -> dropout mapping is a STATED ASSUMPTION (real per-event timing
does not exist in AACT). It is realistic-strength and NOISY: attendance and the dropout event
share a latent disengagement state but each carries its OWN independent noise, so the trajectory
CORRELATES with dropout without DETERMINING it. A non-separability check fits a logistic
classifier on the engagement features and asserts the AUC sits in a realistic band
(<= 0.85): **AUC = 0.767** here. A near-perfect AUC would mean a
leaky planted deterministic rule and fails the build loud.

## Literature-fixed effect signs
younger age -> more dropout (-); higher REAL baseline HbA1c -> more dropout (+); placebo/control
arm -> more dropout (+); denser schedule & longer planned duration -> more dropout (+); more
sites -> more dropout (+, real T2D sign).

## Survival augmentation (calendar geometry + right-censoring)
Augmented by `scripts/augment_t2d_survival.py` (seed: 20240601, snapshot: 2026-06-05).
Three columns added to `participants.parquet` in-place:
- `enrollment_day` (int): Uniform[0, planned_duration_days/3) — deterministic from seed.
- `time_to_event` (float): study-days to observed dropout or censoring (hard lower bound = 1).
- `event_observed` (int 0/1): 1 = dropout observed before admin cutoff, 0 = right-censored.

The LATENT dropout process (column `dropped`) is unchanged; adding calendar geometry turns
some observed `dropped=True` rows into `event_observed=0` when the snapshot cutoff precedes
the latent event. Do NOT retune the hazard to recover the latent rate from the observed rate.

Non-informative censoring check: admin-cutoff indicator is independent of baseline
covariates and the planted disengagement signal (all |r| < 0.15 — PASS).

## Files
- `participants.parquet` — one row per synthetic participant (baseline covariates, imputed
  flags, dropped/censored, dropout reason, dropout visit, enrollment_day, time_to_event,
  event_observed).
- `engagement.parquet` — long per-visit attendance trajectory (full, pre-censoring).
- `engagement_censored.parquet` — engagement rows truncated at time_to_event per participant.
- `calibration_targets.json` — the real (+ labelled-prior) targets.
- `calibration_report.json` — REAL vs SYNTHETIC vs TOLERANCE, PASS/FAIL (v1, preserved).
- `calibration_report_v2.json` — v1 results + latent_dropout_matches_real + censoring diagnostics.
