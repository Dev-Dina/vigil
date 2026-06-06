# Data Spec

## Decisions (fixed)
- Source: ClinicalTrials.gov / AACT — **build-time ingestion only**, never live, never agent-reached.
- Real aggregate outcomes are modelled directly; those statistics calibrate a SYNTHETIC
  per-participant cohort (clearly labelled) for the deep-learning layer.
- Claim is method validity / partner-readiness, never clinical prediction. No PHI.

## Evaluation contract
**One artifact per purpose. Choosing the wrong artifact type is a spec violation.** The principle,
stated once: **golden = transforms, eval set = RAG, held-out split = models.**

| Layer | Test artifact | What it is | Hard rules |
|---|---|---|---|
| **Transforms** (ingestion raw→`ref_*`) | **Golden set** | A frozen slice of **real public** AACT input committed alongside its **expected** cleaned output; the transform is asserted to reproduce it (`assert_frame_equal`). | Real public trial-level data only. **No synthetic, no PHI.** No held-out split, no model metrics. |
| **Models** (Phase 3) | **Held-out split** | A **temporal, group-disjoint-by-`nct_id`** held-out evaluation — one trial's participants never span splits, evaluation is on later time / unseen trials. | Fixed metrics: **PR-AUC (primary)**, recall@fixed-precision, calibration/Brier, lead-time gain — **reported per `sponsor_class`**. **No golden set. No rebalancing.** Scalers/encoders **fit on train only**. |
| **RAG** (app assistant + demo receptionist, Phase 5 / demo) | **Eval set** | A set of **question → expected-grounding** pairs scored for **faithfulness** + **citation accuracy**. | **No golden set.** App-RAG and demo-RAG get **separate** eval sets, specced in `rag.md` at that phase. |

## Raw ingestion
AACT (CTTI's "Aggregate Analysis of ClinicalTrials.gov") is a published Postgres mirror of
ClinicalTrials.gov. We pin a **monthly static snapshot** (its date is the provenance key) and
extract server-side SQL — never a live/runtime call.

**Population filter** (the studies the real dropout statistics are computed from): interventional
studies with reported participant-flow results — `studies.study_type = 'INTERVENTIONAL'`,
`calculated_values.were_results_reported = true`, and a `result_groups` row of
`result_type = 'Participant Flow'` with `milestones` present. This is the only slice where real
per-arm started/completed/dropout counts exist.

Tables → columns pulled:
- `studies`: nct_id, study_type, overall_status, phase, enrollment, enrollment_type, start_date,
  primary_completion_date, completion_date, number_of_arms
- `calculated_values`: nct_id, actual_duration, number_of_facilities, were_results_reported,
  minimum_age_num, maximum_age_num
- `eligibilities`: nct_id, gender, minimum_age, maximum_age, healthy_volunteers, sampling_method
- `designs`: nct_id, allocation, intervention_model, primary_purpose, masking
- `browse_conditions`: nct_id, mesh_term (→ therapeutic area); fallback `conditions.downcase_name`
- `interventions`: nct_id, intervention_type, name
- `sponsors`: nct_id, agency_class, lead_or_collaborator
- `countries`: nct_id, name, removed (→ #countries); `facilities`: nct_id, country (→ #sites)
- `result_groups`: id, nct_id, ctgov_group_code, result_type, title (the arm key for flow data)
- `milestones`: nct_id, result_group_id, ctgov_group_code, title, period, count
  (titles: STARTED / COMPLETED / NOT COMPLETED — the participant-flow counts)
- `drop_withdrawals`: nct_id, result_group_id, ctgov_group_code, period, reason, count
  (the coded dropout reasons — the real retention signal)

**Extraction**: one deterministic query per table, `ORDER BY nct_id` (then group_id), fetched in
NCT-id batches. Write newline-delimited JSON, one object per study with nested arms/milestones/
withdrawals, to `data/raw/aact/<snapshot_date>/<table>.ndjson`. Alongside, write
`manifest.json`: snapshot date, AACT version, source URL, the exact SQL + filter predicates, and
per-table row counts. `data/` is git-ignored — raw extracts are never committed.

## Cleaned schema
Raw → clean is validated at the boundary with Pydantic; a missing/malformed field raises a typed
`DataValidationError` and the offending record is written to the quality report — never silently
coerced or dropped. Cleaned output is Parquet under `data/clean/`. Three tables.

**Naming rule (stated once, applies everywhere).** AACT-derived tables are prefixed **`ref_`** —
they are **public clinical-trials reference data, RLS-exempt, contain no PHI**, and are distinct
from the operational tenant-scoped entities (`trial`, `site`, `participant`, …) which are
**sponsor-scoped with RLS on**. The `ref_` prefix is the visible marker of that boundary: a
`ref_*` table is never sponsor-keyed and an operational table never lives in the AACT pipeline.

**Enum format (CTGOV2).** The pinned hosted-AACT snapshot serves ClinicalTrials.gov's **CTGOV2**
enum codes — uppercased machine tokens (`INTERVENTIONAL`, `PHASE2`, `RANDOMIZED`, `ALL`,
`NONE` …), not the legacy human-readable strings. Those CTGOV2 codes **are** the canonical values
stored in `ref_*`; `ingestion/ctgov_enums.py` normalises any raw token onto the spec's controlled
set, and a token with no spec category (e.g. an unexpected code) returns `None` so the clean stage
fails loud and records it — never a silent coercion. The controlled sets below are the CTGOV2
tokens.

**`ref_trial`** (one row per NCT):
| column | type | rule |
|---|---|---|
| nct_id | str PK | `^NCT\d{8}$` |
| study_url | str (derived) | `https://clinicaltrials.gov/study/{nct_id}` — computed, not stored raw; for UI / citation linking |
| study_type | enum | `INTERVENTIONAL` only (post-filter) |
| phase | enum | `EARLY_PHASE1` / `PHASE1` / `PHASE1/PHASE2` / `PHASE2` / `PHASE2/PHASE3` / `PHASE3` / `PHASE4` / `NA` |
| therapeutic_area | str | mapped from MeSH; unmapped → `OTHER` (original logged) |
| enrollment | int | > 0 |
| n_arms | int | ≥ 1 |
| n_sites, n_countries | int | ≥ 0 |
| planned_duration_days | int | > 0 = primary_completion_date − start_date |
| actual_duration_days | int? | nullable |
| allocation | enum | from `designs`: `RANDOMIZED` / `NON_RANDOMIZED` / `NA` |
| intervention_model | enum | `PARALLEL` / `CROSSOVER` / `SINGLE_GROUP` / `FACTORIAL` / `SEQUENTIAL` / `NA` |
| masking | enum | `NONE` / `SINGLE` / `DOUBLE` / `TRIPLE` / `QUADRUPLE` / `NA` |
| primary_purpose | enum | `TREATMENT` / `PREVENTION` / `DIAGNOSTIC` / `SUPPORTIVE_CARE` / `SCREENING` / `HEALTH_SERVICES_RESEARCH` / `BASIC_SCIENCE` / `DEVICE_FEASIBILITY` / `ECT` / `OTHER` / `NA` |
| min_age_years, max_age_years | float? | nullable |
| gender | enum | `ALL` / `FEMALE` / `MALE` |
| healthy_volunteers | bool | |
| sponsor_class | enum | INDUSTRY / NIH / OTHER_GOV / ACADEMIC_OTHER |
| results_reported | bool | true for the modelled cohort |

**`ref_arm`** (one row per trial-arm):
| column | type | rule |
|---|---|---|
| arm_id | str PK | `nct_id` + ctgov_group_code |
| nct_id | str FK | |
| arm_type | enum | Experimental / Active Comp / Placebo Comp / Other |
| started | int | ≥ 0 (milestone STARTED) |
| completed | int | ≥ 0 (milestone COMPLETED); **completed ≤ started** |
| not_completed | int | = started − completed (≥ 0) |
| dropout_rate | float | not_completed / started, **∈ [0, 1]** |

**`ref_withdrawal_reason`** (one row per trial-arm-reason):
| column | type | rule |
|---|---|---|
| nct_id, arm_id | str FK | |
| reason | enum | controlled vocab (below) |
| count | int | ≥ 0 |

Reason controlled vocabulary (normalised from free-text `drop_withdrawals.reason`):
`ADVERSE_EVENT, LACK_OF_EFFICACY, WITHDRAWAL_BY_SUBJECT, LOST_TO_FOLLOWUP, PHYSICIAN_DECISION,
PROTOCOL_VIOLATION, DEATH, PREGNANCY, NONCOMPLIANCE, STUDY_TERMINATED, ADMINISTRATIVE, OTHER`.
Unmapped strings → `OTHER` **with the original text recorded** in the quality report (no silent loss).

- `STUDY_TERMINATED` — the trial (or an arm) was stopped by the sponsor/DSMB, not a
  participant-level retention event (e.g. "study terminated by sponsor", "DSMB design
  modification", "arm closed"). It is a real exit in the flow counts but is **sponsor-driven**.
- `ADMINISTRATIVE` — survey/data-collection bookkeeping with no clinical retention meaning
  (e.g. "did not complete mid-study survey", "data incomplete").
- **Censoring / still-ongoing is NOT a dropout reason.** Strings denoting right-censoring or
  continuation ("participants entered open-label period", "ongoing", "still on study", "completed
  per protocol → rolled over") are **excluded from the withdrawal reason-mix** entirely — neither a
  dropout-reason category nor `OTHER`. They are recorded separately in the quality report as
  excluded-censoring, never counted toward `not_completed`-explained dropout.

Cross-record validations (fail LOUD): `completed ≤ started` per arm; `sum(withdrawal counts) ≤
not_completed` per arm (the unexplained remainder is reported, not hidden); `dropout_rate ∈ [0,1]`;
`enrollment > 0`; `planned_duration_days > 0`. Any violation → typed error + quality-report row.

## Synthetic cohort
AACT gives **aggregates only** (per-arm counts), but the deep-learning layer needs per-participant
longitudinal sequences. So we generate a synthetic per-participant cohort, clearly labelled, whose
aggregates reproduce the real AACT statistics. It proves the METHOD; it is never a clinical claim.

**Generation rules**
- **Deterministic**: a single fixed seed (`SYNTHETIC_SEED`, recorded in the manifest); all RNG
  derives from one `numpy.random.Generator(seed)`. Regeneration is bit-identical.
- **Clearly labelled**: every row carries `synthetic = True`; all output lives under
  `data/synthetic/` (never mixed with `raw`/`clean`), with a README stating "SYNTHETIC — calibrated
  to aggregate AACT statistics, NOT real participants, method-validity only, no PHI."
- Per real trial, sample `enrollment` participants across its arms; draw static covariates from
  per-stratum distributions (stratum = phase × therapeutic_area × sponsor_class). Where AACT lacks
  a covariate, calibrate to published trial demographics and **label the assumption** in the manifest.
- Draw a latent dropout propensity per participant, then a dropout event and time-to-dropout, so
  that aggregated back up the cohort hits the targets below. Generate engagement time-series whose
  *deteriorating trajectory* precedes dropout (the signal the model must learn) with realistic noise.

**Calibration targets** — synthetic aggregates MUST match the real AACT-derived values within
tolerance; the calibration report prints real vs synthetic vs tolerance with PASS/FAIL and **fails
loud** on any miss:
1. Overall dropout rate (mean not_completed/started) — within **±1 pp**.
2. Dropout rate by stratum (phase, therapeutic_area, sponsor_class, blinded vs open, single- vs
   multi-site) — within **±3 pp** per stratum.
3. Dropout-**reason** mix over the controlled vocab — categorical distance (chi-square) below
   threshold.
4. Dropout **timing**: early-vs-late split derived from milestone periods — hazard shape matches.
5. Covariate→dropout associations (e.g. longer duration, more sites, certain phases ↑ dropout) —
   **sign preserved** and effect size approximately matched.
6. Enrollment and arm-count marginals per stratum — matched.

## Features
- Static: age, baseline severity, socioeconomic proxy, travel friction, prior-trial experience, comorbidities.
- Time-varying (the DL signal): diary-completion rate + trend, days since last entry,
  reminder-response latency, app-opens, missed visits, symptom-log frequency. Signal = change over time.

**Prediction task**: at a decision time *t* during a participant's active enrollment, predict
dropout within a fixed horizon *H* (default 28 days). Label = a dropout event in `[t, t+H)`.
Every feature is computed from observations strictly before *t*.

**Static features** (known at enrollment, constant): age (years); baseline_severity (calibrated
score); socioeconomic_proxy (labelled synthetic proxy); travel_friction (distance-to-site /
multi-site proxy); prior_trial_experience (count); comorbidity_count; plus trial context
(phase, therapeutic_area, arm_type, planned_duration_days, n_sites).

**Time-varying features** — each computed over trailing windows ending at *t* (exclusive), emitted
as: current value, 7-day aggregate, 28-day aggregate, and a slope/delta capturing deterioration:
diary_completion_rate; days_since_last_entry (at *t*); reminder_response_latency (hours);
app_opens (count); missed_visits (recent + cumulative); symptom_log_frequency. The signal is the
**trend**, not the level — a declining 7d-vs-28d ratio is the prototype dropout precursor.

**Leakage rules (non-negotiable)**
- **No future data**: every feature uses only observations with timestamp `< t`; no window may
  overlap the label horizon `[t, t+H)`.
- **No outcome-caused inputs**: the dropout event and anything it produces — the final missed
  visit, the withdrawal record, post-dropout silence, `days_since_last_entry` measured after the
  last-ever entry — are excluded. Features stop at the last observation before *t*.
- **No target-derived leakage**: cohort dropout rates, calibration targets, or any statistic
  computed from outcomes are never per-participant features.
- **Group split by trial**: train/val/test split on `nct_id` so one trial's participants never
  span splits. Scalers/encoders are fit on **train only**.
- **`synthetic` is metadata, never a feature.**
- **Censoring**: participants still active at data cutoff are right-censored and excluded from
  labels they cannot have — never silently labelled "no dropout".

A pipeline **leakage check** asserts (fails loud): max feature timestamp `< t` for every sample;
no `nct_id` appears in two splits; no feature is outcome-derived.

## Done when
One command runs raw -> clean -> synthetic, emits a data-quality report, validates against this spec.
