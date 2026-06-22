# Scoring Spec

## Decisions (fixed)
- **Single feature path.** The live scoring pipeline calls the SAME `ContractTransformer` (fit
  artifact from the committed model version) and the SAME leakage assertions
  (`assert_feature_time_before_t`, `run_smoke`) as training. There is no separate scoring feature
  path. Any schema drift from the training contract is a hard error, not a silent fallback.
- **Champion model.** The sequence model (Phase 3, Step 3) is the operational scorer for the
  synthetic T2D demo. The discrete-time hazard and pan-indication baselines are evaluation
  artifacts only. Model routing (champion / challenger / shadow) is governed by the routing spec
  (`/specs/routing.md`); this spec fixes the contract, not the model identity.
- **Async only.** Scoring runs in the Arq worker; it is NEVER executed synchronously in the
  request path. The request path returns `202 Accepted + job_id` immediately.
- **Tenancy = sponsor.** In Vigil "tenant" = sponsor (`sponsor_id` in every table). The scoring
  spec uses `sponsor_id` throughout. Cross-tenant isolation is enforced at the Postgres RLS layer,
  not by application-layer `WHERE sponsor_id = $1` filtering (which may exist but is not the hard
  guarantee).
- **Demo boundary.** The ONLY demo-scoped component is the engagement-event injection entry point
  (`POST /scoring/inject_events`). All downstream scoring, writeback, and RLS enforcement are the
  real operational path — demo injection produces real inputs that flow through the real pipeline.

## Scoring contract

### Feature pipeline (invariant: identical to training)
The scoring job MUST call the training-time guards in this order before any inference:

1. Load the committed `ContractTransformer` fit artifact for the registered model version. Never
   re-fit at scoring time; a missing artifact is a hard error.
2. `assert_feature_time_before_t(feature_names, t)` — no future-leaking features.
3. `run_smoke(matrices)` — fires before every inference call; fails loud on any violation.
4. `assert_no_outcome_features(feature_names)` — outcome and provenance columns are NEVER features.

Any feature not present in `models/features/contract.py` at training time is forbidden at scoring
time. A schema change requires a model version bump and a re-fit; there is no fallback path that
silently drops or imputes an unexpected column.

### Output schema per participant
```python
class ParticipantScore(BaseModel):
    participant_id: str
    sponsor_id: str                              # hard tenant key; present on every row
    trial_id: str
    site_id: str
    risk_score: float = Field(ge=0, le=1)
    risk_band: Literal["high", "medium", "low"]  # >0.6 high | >0.3 medium | else low
    top_factors: list[str]  # top-3 signed temporal attributions (gradient / SHAP; human-readable)
    reasons: list[FactorContribution]            # full signed list for RiskExplanation endpoint
    model_version: str      # committed artifact ref, e.g. "sequence_v1.0:<git_sha>"
    model_card_ref: str     # path to the authoritative model card
    synthetic: bool         # True when the input cohort is synthetic (method demo)
    computed_at: datetime
```
`risk_band` thresholds are operational decisions; the spec fixes the field and the initial values
(`high > 0.6`, `medium > 0.3`). Threshold changes require a model card update and a version bump.

`reasons` / `top_factors` map to `RiskExplanation.factors` in `/specs/api.md`; the scoring job
populates them from the model's temporal attribution at scoring time.

### Output calibration (Gate 9.7a — ratified)
The champion sequence model's **raw** sigmoid outputs are **compressed** (the trained LSTM
discriminates correctly but its per-decision-point probabilities top out near ~0.54, so the
`> 0.6` HIGH band is never reachable from the real model). The champion therefore carries a
**monotonic probability calibrator** persisted **with the model artifact** and applied at score
time, so the emitted `risk_score` spans a usable probability range and `> 0.6` is reachable from
the real model — **without changing the threshold and without an override**.

Fixed decisions:
- **Method: isotonic regression** (monotonic, non-parametric). Platt/logistic was evaluated and
  rejected for this champion: the LSTM is already globally near-calibrated (ECE ≈ 0.008, Platt
  slope ≈ 1.06), so a global sigmoid cannot stretch the compressed top past 0.6. Isotonic
  corrects the **local under-confidence at the top of the score range** (the highest raw-score
  decision points have an **asserted** empirical dropout rate ≈ 0.74 — a build-time observation,
  not a figure persisted to a committed artifact — well above their raw ≈ 0.5), which is
  what makes `> 0.6` both reachable **and honest**.
- **Provenance note — build-time figures.** The calibration *quality* numbers cited here and in
  `data/models/t2d/model_card.md` (the ECE ≈ 0.008 near-calibration figure, the ECE/calibrated-Brier
  raw → calibrated, the ROC-AUC raw → calibrated, and the ≈ 0.74 empirical rate) are **reported at
  build time and are NOT regenerable from a committed artifact** — unlike the headline test metrics in
  `sequence_metrics.json`, which are. The isotonic **mechanism** is reproducible (the
  `raw_prob → calibrated_prob` knot-map is persisted in the `.pt`); only these quality sub-metrics
  lack a backing JSON, the ECE/calibrated-Brier are validation-fold figures inside the `.pt`
  `calibration_report`, and the ≈ 0.74 rate is asserted, not computed.
- **Fit split: the held-out `val` fold ONLY** — the temporal, group-disjoint-by-`nct_id` fold
  that sits between train and test (`/specs/data.md` "Evaluation contract"). The calibrator is
  fit on data **disjoint from both the LSTM's training fold AND the reported test fold**; the
  artifact freezes `train_nct_ids` / `val_nct_ids` / `test_nct_ids` so the disjointness is
  asserted hermetically. The calibrator consumes **no outcome at score time** — it is a fixed
  `raw_prob → calibrated_prob` map fit once at build time.
- **Discrimination is UNCHANGED.** Calibration is monotonic, so it preserves ranking: test
  ROC-AUC is invariant (Δ ≈ −5e-5) and the per-decision-point PR-AUC over the preserved ranking
  is unchanged. Calibration **re-maps the probability scale; it does not manufacture
  discrimination.** A material AUC move would mean a non-calibration change leaked in and is a
  hard stop. This is the honesty invariant of this gate.
- **Attribution is on the model's own (pre-calibration) output.** The `top_factors` / `reasons`
  occlusion deltas (§ Phase 9, Gate 9.1) are computed on the LSTM's raw output — the quantity the
  network actually produces — so they stay meaningful (isotonic flat regions never zero them) and
  leakage-safe (only the model's `SEQ_NUMERIC` input channels are perturbed). Calibration is a
  monotone post-hoc remap that does not reorder feature importances.
- **Version bump (required).** Calibrating the output changes the probability semantics of every
  score row, so the calibrated champion is a **new model version** (`sequence_v1.1:demo`),
  recorded in the t2d model card with the method, the fit split, and the
  discrimination-vs-calibration framing. The HIGH/MEDIUM thresholds (`> 0.6` / `> 0.3`) are
  **unchanged**; a threshold change would still require its own card update + version bump.
- **Synthetic provenance unchanged.** This is the synthetic-demo regime; a calibrated score on
  synthetic engagement is still stamped `synthetic = True`.

## Writeback

### `participant_score` table — APPENDED history (not upsert-over-current)
Every scoring run **APPENDS** a new timestamped row to `participant_score`: one row per
`(participant_id, model_version, run)`, retaining all prior rows as risk **history** (the
substrate the risk-history endpoint and the sparkline read). Row **identity is the surrogate
`id` PK**; history is **ordered by `computed_at`**. There is **no** `UNIQUE(participant_id,
model_version)` — that constraint forced upsert-overwrite and is REPLACED by a composite index
`(participant_id, model_version, computed_at DESC)` serving the newest-row and history-range
queries. (Surrogate-id + index chosen over `UNIQUE(participant_id, model_version, computed_at)`
so two rows sharing a `computed_at` never raise — the run, not the timestamp, is the identity.)
The table carries `sponsor_id` and is protected by the default sponsor RLS policy
(`/specs/domain.md`). Schema:

```sql
CREATE TABLE participant_score (
    id             uuid        PRIMARY KEY DEFAULT gen_random_uuid(),
    participant_id text        NOT NULL,
    sponsor_id     text        NOT NULL,  -- RLS key; indexed
    trial_id       text        NOT NULL,
    site_id        text        NOT NULL,
    risk_score     float       NOT NULL CHECK (risk_score BETWEEN 0 AND 1),
    risk_band      text        NOT NULL CHECK (risk_band IN ('high','medium','low')),
    top_factors    text[]      NOT NULL,
    reasons        jsonb       NOT NULL,  -- full FactorContribution list
    model_version  text        NOT NULL,
    model_card_ref text        NOT NULL,
    synthetic      bool        NOT NULL DEFAULT false,
    computed_at    timestamptz NOT NULL DEFAULT now()
    -- NO UNIQUE(participant_id, model_version): rows APPEND as history.
);
-- RLS: standard sponsor-scoped policy (same as every operational table). UNCHANGED.
-- Index on (sponsor_id, trial_id) for the cohort query hot path.
-- Index on (sponsor_id, risk_band) for band-filtered triage views.
-- Index on (participant_id, model_version, computed_at DESC) for newest-row + history range.
```

Relation to API: `GET /cohort` reads `risk_score`, `risk_band`, `top_factors` from this table
(via the scoped repository). `GET /participants/{id}/risk` reads the full `reasons` list.

### Champion-only **+ latest** surfacing
Clinical reads (`GET /cohort`, `GET /participants/{id}/risk`) surface the **NEWEST champion row
per participant** — the champion-only allowlist rule (`/specs/routing.md § (i)`) AND `max(
computed_at)`. Older champion history rows, and all shadow/challenger rows, **never** surface to
clinical reads. The denorm cache (`participant.risk_score`/`risk_band`) is still written ONLY by
the champion run and reflects the latest champion score.

### Audit log on every write
Every writeback (each appended row) appends a row to the existing `audit_log` table:
- `action: "score_writeback"`
- `actor_id:` the Arq job id (not a human user; scores are computed)
- `resource_type: "participant_score"`, `resource_id: participant_id`
- `sponsor_id:` the scoped tenant
- `payload: {model_version, synthetic, n_rows_written}` — no PII, no risk values in the log.
  Append semantics: a re-run does NOT overwrite — it appends a new point, so each run records
  `n_rows_written` rows (1 per participant per run) rather than mutating a prior row in place.

## Tenancy

### Sponsor = hard tenant boundary (consistent with `/specs/domain.md`)
`sponsor_id` is the tenant key on `participant_score` and every other operational table. The
phrase "tenant → nct_ids" maps to: **`sponsor_id` → the set of `trial.nct_id`s belonging to that
sponsor** — resolved from the tenant-scoped operational `trial` table (RLS-protected), NOT from
the RLS-exempt `ref_trial` table (which is public AACT reference data shared across all tenants).

### Role model: capabilities layered on top of the tenancy key
**Roles are capability grants, not a tenancy mechanism.** `sponsor_id` (RLS) is the hard tenant
boundary; roles define what a user may DO within (or across, for CRO staff) that boundary. A
CRO user may hold grants spanning multiple sponsors via `assignment_grant` rows — in every case
the per-sponsor RLS still fires independently. Platform users (`platform_admin`, `auditor`) carry
`scope: []` and reach only platform/global tables; the sponsor RLS therefore returns zero rows
for them on any sponsor-scoped table.

Role/scope is enforced in two layers: (1) Postgres RLS policies keyed on `sponsor_id` (and
optionally `trial_id`/`site_id` where DB policies support it); (2) the API auth dependency
checks the JWT `role` + `scope` tuples against the requested resource. Both layers must agree;
RLS is the hard guarantee.

The seven roles from `/specs/domain.md` and their scoring-specific access:

| Role | Tenant level | Scoring read | Scoring trigger | Notes |
|---|---|---|---|---|
| Sponsor oversight | own sponsor, all trials | `GET /cohort`, `/participants/*/risk` for own sponsor | `POST /scoring/trigger` for own trials | full sponsor-level view; no identity access |
| Study / project manager (CRO) | assigned sponsors + trials | cohort + risk within assignment scope | trigger for assigned trials | scope = `assignment_grant` union |
| CRA / monitor (CRO) | assigned sites within trials | cohort + risk for assigned sites | — | read-only on scoring data |
| Principal investigator | own site + trial | cohort + risk + identity for own site | log interventions | `identity` field populated (site role) |
| Coordinator (CRC) | own site + trial | cohort + risk + identity for own site | log interventions; daily triage | `identity` field populated (site role) |
| ML / platform admin | models, monitoring, cost only | `GET /monitoring/*` (aggregate model health, score distributions, drift) only; **no** `GET /cohort` or `/participants/*` | `POST /scoring/trigger` (model ops); manage model registry | NO access to identifiable participant data (`/specs/domain.md`); `scope: []` → RLS returns empty on sponsor-scoped tables |
| Auditor | read-only, all activity | `GET /monitoring/*`, audit logs, `message_events` | — | no write actions of any kind; cannot trigger scoring |

`trial_id` and `site_id` are sub-scope **filters** layered on top of the `sponsor_id` RLS key.
Narrowing a request to a specific trial or site is an additional restriction, never an elevation.
A trial-scoped CRO user cannot widen to the full sponsor by omitting `trial_id`.

### Scope resolution at job time
The scoring worker resolves the `sponsor_id → nct_id` set from the DB at job time, using the
tenant-scoped session opened by the job's own verified scope. The mapping is never passed in the
job payload (client-asserted scope is always rejected). The session variable is set before the
first query and covers the full writeback transaction.

### Cross-tenant isolation: DB-enforced, not app-enforced
`participant_score` carries `sponsor_id` and is subject to the Postgres RLS policy. A missing
`WHERE sponsor_id = $1` in application code cannot leak across sponsors; the engine blocks it.
App-layer filtering may co-exist but is NOT the hard guarantee. This matches the invariant
established in `/specs/domain.md`.

## Execution model

### Async scoring job (Arq worker)
- Job name: `score_trial(trial_id: str, model_version: str | None = None)`
- `model_version=None` resolves to the registered champion at job time.
- Full job sequence: resolve scope → load cohort → build features → `run_smoke` →
  sequence-model inference → compute attributions → writeback → audit log.
- On failure: exponential backoff + jitter (`/specs/infra.md` resilience rules). The writeback
  APPENDS a timestamped history row (no unique key — see § Writeback), so a retry APPENDS a new
  point rather than overwriting; it is NOT upsert-idempotent. Retry is safe for correctness
  (each row is independent, RLS-scoped, audited) but may leave a duplicate near-coincident
  history point — a cosmetic artifact, never a cross-tenant or non-champion leak.
- The request path NEVER blocks on scoring. `POST /scoring/trigger` enqueues and returns
  `202 Accepted`.

### Scoring API endpoints (append to `/api/v1`)
These endpoints follow the same auth/scope dependency pattern as all other routes in
`/specs/api.md`.

| Method · Path | Request | Response | Notes |
|---|---|---|---|
| `POST /scoring/trigger` | `ScoringTriggerIn` | `JobAccepted (202)` | enqueues; never inline |
| `GET /scoring/jobs/{job_id}` | path | `ScoringJobStatus` | poll completion |
| `POST /scoring/inject_events` | `InjectEventsIn` | `204` | demo-mode only; `404` in prod |

```python
class ScoringTriggerIn(BaseModel):
    trial_id: str           # must be within caller's scope; 403 scope_denied if not
    model_version: str | None = None   # null → champion

class ScoringJobStatus(BaseModel):
    job_id: str
    trial_id: str
    status: Literal["queued", "running", "done", "failed"]
    n_scored: int | None = None
    error: str | None = None
    triggered_at: datetime
    completed_at: datetime | None = None
```

`JobAccepted` reuses the type already defined in `/specs/api.md`.

## Engagement (visit trajectory) input

### Engagement table (prose schema)

The `engagement` table holds per-participant, per-visit trajectory rows that back the sequence
model's input tensors. It is a **tenant-scoped operational table** — not `ref_`-prefixed, not
RLS-exempt — and follows the same sponsor-boundary rules as `participant_score`.

Columns:
- `id` — UUID primary key
- `sponsor_id` — UUID NOT NULL; the hard tenant key, FK to `sponsor`; present on every row
  for RLS enforcement
- `participant_id` — UUID NOT NULL; FK to `participant`; cascade delete on participant removal
- `trial_id` — UUID NOT NULL; redundant denorm (FK to `trial`) carried for index efficiency on
  the `(sponsor_id, trial_id)` hot-path query; consistent with the `participant_score` pattern
- `site_id` — UUID NOT NULL; redundant denorm (FK to `site`); same rationale
- `visit_index` — integer NOT NULL ≥ 0; zero-based position in the participant's scheduled visit
  sequence; this is the sequence model's time axis
- `visit_timestamp` — timestamptz NOT NULL; scheduled or actual visit date; required for
  `assert_feature_time_before_t` leakage enforcement at scoring time
- `attended` — bool NOT NULL; whether the participant attended this visit
- `missed` — bool NOT NULL; inverse of `attended`; stored redundantly for read clarity
- `cumulative_missed` — integer NOT NULL ≥ 0; running count of missed visits through and
  including this visit
- `consecutive_missed` — integer NOT NULL ≥ 0; length of the consecutive-miss run ending at or
  before this visit; resets to zero on an attended visit
- `synthetic` — bool NOT NULL DEFAULT false; `True` on every row originating from the demo
  injection path or the synthetic T2D parquet; propagates to `participant_score.synthetic` for
  any score derived from this engagement

Unique key: `(participant_id, visit_index)` — exactly one row per participant per scheduled
visit slot.

**Static covariates home.** The LSTM's static context — `age_years`, `hba1c_pct`, `bmi`,
`sex`, `phase`, `arm_type`, `n_sites`, `planned_duration_days` — is split between two sources.
The per-participant fields (`age_years`, `hba1c_pct`, `bmi`, `sex`) live on the **`participant`
table** (the enrollment record). `planned_duration_days` is **trial-level**: it is derived from
`primary_completion_date − start_date` and lives on (or is read from) the `trial` record at
feature-assembly time; it is NOT copied onto the participant row and carries no
`*_baseline_imputed` flag. `phase`, `arm_type`, and `n_sites` are similarly trial/arm-level.
Feature assembly reads per-participant covariates from `participant`, trial/arm context from
`trial`, and trajectory from `engagement`; there is no fourth source. The single-feature-path
principle requires all three reads to be gated by the same `ContractTransformer` fit artifact
and leakage assertions as training. The `participant` table therefore requires additional columns
(`age_years`, `hba1c_pct`, `bmi`, `sex`, and the associated `*_baseline_imputed` provenance
flags for the imputed-capable subset) to be added in B2a-build alongside the engagement
migration.

**Covariate provenance invariant (hard).** Every imputed-capable covariate added to
`participant` in B2a-build MUST have a companion boolean column `<col>_baseline_imputed` on the
same row. `True` means the value was imputed (literature-prior or otherwise non-measured);
`False` means it is a real measured value from the source record. A covariate value present
without its companion provenance flag is a **spec violation**, not a nice-to-have: presenting an
imputed placeholder as a measured patient value is the provenance collapse the entire platform
guards against (per PHASE3_CARD: BMI ~80% imputed, HbA1c ~55% imputed on the synthetic cohort).

The imputed-capable set is exactly these three participant columns:

- `age_years` → companion `age_years_baseline_imputed`
- `hba1c_pct` → companion `hba1c_pct_baseline_imputed`
- `bmi` → companion `bmi_baseline_imputed`

`sex` is always-real: it is a categorical demographic field (male/female/unknown) recorded
directly from the enrollment form; it requires no companion flag. `planned_duration_days` is
trial-level (NOT participant-level); it never lives on the participant row and carries no flag.
B2a-build must create companion flags only for the three columns above, no more, no fewer.

This provenance pairing is part of the single-feature-path contract. The `ContractTransformer`
and feature-assembly code may consume the covariate value, but the `*_baseline_imputed` flag is
**NEVER dropped silently**. The flag itself is provenance metadata — it is NOT a predictor fed
to the model and NOT a leakage shortcut to the outcome. It must be excluded from the model's
feature matrix via `EXCLUDED_FROM_FEATURES` (or the equivalent ContractTransformer exclusion
list), consistent with the same discipline that excludes `synthetic` and all outcome columns.

### Tenancy

`engagement` holds per-sponsor participant data. It is **sponsor-RLS'd under the same guarantee
as `participant_score`**: FORCE RLS, transaction-scoped GUC set before every query, fail-closed
on null sponsor (query returns zero rows), no application-layer `WHERE sponsor_id = $1` as the
hard guarantee — Postgres RLS is the only guarantee. `engagement` is **NOT** on the RLS-exempt
list from `/specs/domain.md`. It is a member of `TENANT_TABLES` in the ORM and migration layer,
alongside `trial`, `site`, `participant`, `intervention`, and `participant_score`.

### Feature-time and leakage discipline

`engagement` is the most leakage-prone surface in the system: it contains visit-timed
observations, and the prediction horizon *t* changes per decision point. The following rules are
non-negotiable for any feature built from engagement:

1. **Future-exclusion**: features built for a prediction at horizon *t* use ONLY engagement rows
   where `visit_index < t` (equivalently, `visit_timestamp < scheduled_time(t)`).
   `assert_feature_time_before_t` fires before every inference call and enforces this.
2. **Single feature path**: the live scoring path builds engagement features through the SAME
   `ContractTransformer` fit artifact, `build_tensors`, and `_seq_feature_frame` as training.
   There is no demo-only or scoring-only parallel feature path.
3. **Leakage guard order**: `assert_feature_time_before_t` → `run_smoke` →
   `assert_no_outcome_features` — in that exact order, before every inference call.
4. **Forbidden engagement features**: `dropped`, `censored`, `dropout_visit_index`,
   `dropout_reason` (outcome columns), and `miss_probability` (the generator's latent hazard)
   are NEVER engagement features. `assert_no_outcome_features` enforces this.

### Synthetic provenance

Every engagement row from the demo injection path or the synthetic T2D parquet carries
`synthetic = True`. Any score derived from synthetic engagement MUST write
`participant_score.synthetic = True`. This propagation is end-to-end and non-optional: a
participant whose entire engagement history is synthetic never produces a score labeled
`synthetic = False`. The scoring worker reads `engagement.synthetic` across the trial's rows,
takes the logical OR, and stamps the score output.

### Seed / demo bridge (open question)

Demo participants in the operational DB (UUID-keyed `coded_ref` strings such as `"A-0001"`) have
no engagement rows and no mapping to the AACT-style `participant_id` values in
`data/synthetic/t2d/engagement.parquet`. B2a-build must define an explicit bridge: either (a) a
seed script that selects a representative subset of synthetic parquet rows and inserts them into
the `engagement` table linked to the demo participants' UUIDs, or (b) a deterministic mapping
function that assigns a synthetic parquet participant's trajectory to each demo DB participant by
index or stable hash. **The shape of that bridge — which synthetic participants map to which demo
UUIDs, and how — is an open question for the build author to resolve and document before
committing B2a-build.** It must not be resolved silently.

## Demo-scope boundary

### Only one demo-scoped component
`POST /scoring/inject_events` is the only component that is demo-scoped. It accepts synthetic
engagement events for a trial and is gated behind `DEMO_MODE=true` (Vault-held flag; default
false in production). When disabled it returns `404`. It is unreachable from the public Guide
(`/specs/isolation.md`).

All other components — feature assembly, leakage assertions, sequence-model inference, writeback,
audit log, RLS enforcement — are the unmodified real path. Injected synthetic events flow into
the real pipeline unchanged.

```python
class InjectEventsIn(BaseModel):
    trial_id: str
    events: list[EngagementEvent]  # schema defined in Phase 4 seed fixture
    demo_mode_key: str             # validated against Vault-held demo secret; hard error if wrong
```

## Leakage-test invariants

Extend the existing sacred cross-tenant leakage test (`/specs/data.md` "Evaluation contract"):

### 1. Adversarial cross-tenant isolation on the score table
Create a `participant_score` row under Sponsor A. Authenticate as Sponsor B. Assert:
- `GET /cohort` returns zero rows matching Sponsor A's participant.
- `GET /participants/{id}/risk` returns `404` (or `403`), not a score.

This is the sacred cross-tenant leakage test applied to the scoring writeback table.

### 2. Live-scoring path fires training-time leakage assertions
The scoring job MUST call `run_smoke` and `assert_no_outcome_features` before inference. A test
that deliberately inserts a forbidden column (e.g. `dropout_rate`, `miss_probability`) into the
feature matrix MUST cause the job to raise, not silently proceed or drop the column.

### 3. `synthetic` flag propagates to the DB
If the input cohort has `synthetic=True` rows (from demo injection or the synthetic T2D cohort),
the written `participant_score.synthetic` MUST be `True`. A test that injects synthetic events
and reads back the score via the repository MUST assert `synthetic == True` on the retrieved row.

### 4. ML-admin cannot read participant-level scoring rows
An ML-admin JWT (`role: platform_admin`, `scope: []`) MUST receive `403 scope_denied` on
`GET /cohort` and `GET /participants/{id}/risk`. The sponsor-scoped RLS policy returns zero rows
to `scope: []` users; the API auth dependency must additionally reject the request before
reaching the repository. Both layers are asserted independently:
- Direct DB query under an ML-admin session must return an empty result set on `participant_score`.
- HTTP call to `GET /cohort` with a valid ML-admin token must return `403`, not an empty list.

### 5. Auditor is read-only on all scoring surfaces
An auditor JWT (`role: auditor`) MUST receive `403 scope_denied` or `405 Method Not Allowed` on
any mutating scoring call:
- `POST /scoring/trigger` → `403`
- `POST /participants/{id}/interventions` → `403`
- Any `DELETE` on scoring-adjacent resources → `403`

An auditor MAY successfully call read-only endpoints (`GET /monitoring/*`, `GET /audit_log`).
A test MUST assert both directions: the write rejection AND the read success.

The following invariants are obligations for **B2a-build** (the engagement table migration and
seed bridge). They extend the sacred leakage suite with the engagement-specific surface.

### 6. Cross-tenant isolation on engagement rows
The sacred cross-tenant test applied to `engagement`: create engagement rows under Sponsor A.
Authenticate as Sponsor B. Assert:
- A direct DB query under the Sponsor B session returns zero engagement rows for Sponsor A's
  participants.
- `GET /cohort` and `GET /participants/{id}/risk` return no data derived from Sponsor A's
  engagement, even if Sponsor A and B share the same trial structure.

### 7. Feature-time leakage assertion fires on post-horizon engagement
Build a feature matrix that includes an engagement row with `visit_index ≥ t` (the prediction
horizon). Assert that `assert_feature_time_before_t` raises rather than silently passing. The
scorer MUST NOT produce a score from future-leaking inputs; this failure must be loud.

### 8. Synthetic engagement propagates to synthetic-labeled scores
Insert engagement rows with `synthetic = True` for a demo participant. Trigger scoring. Assert
that the resulting `participant_score.synthetic` is `True`. Insert engagement rows with
`synthetic = False` for a separate participant and assert the score is labeled `synthetic = False`.
Both propagation directions are tested.

### 9. Single-path: live scoring uses the training-time engagement feature assembly
The scoring worker's feature assembly for engagement-backed participants must call `build_tensors`
and `_seq_feature_frame` from `models/t2d/sequence.py` (or the equivalent committed
ContractTransformer path), not a demo-only parallel path. A test that mocks the parallel path
(bypassing the contract) MUST NOT exist; a test that asserts the training-contract functions are
called during scoring MUST exist.

### 10. Covariate provenance flags are mandatory and honest
This invariant is a B2a-build obligation; the tests come in B2a-build (B2a-1).

- A test MUST assert that for every imputed-capable participant covariate (`age_years`,
  `hba1c_pct`, `bmi`) the companion `<col>_baseline_imputed` column is present in both the
  Alembic migration DDL and the `Participant` ORM model. A covariate column present without its
  companion flag fails the test.
- A test MUST cover both flag directions: a synthetically-imputed value carries
  `<col>_baseline_imputed = True` and a value from a real source record carries
  `<col>_baseline_imputed = False`. Both directions must be exercised so the flag cannot
  default-stamp every row one way.
- The `*_baseline_imputed` flags MUST NOT appear in the model's feature matrix as predictors.
  Each `*_baseline_imputed` column name must be present in `EXCLUDED_FROM_FEATURES` (or the
  equivalent ContractTransformer exclusion list). This is consistent with, and an extension of,
  the `assert_no_outcome_features` discipline that governs all non-predictor columns.
- `planned_duration_days` is trial-level; it carries no companion flag and is never added to
  the `participant` table. Any test checking for `planned_duration_days_baseline_imputed` on
  `participant` MUST NOT exist.

## Phase 9 — clinical-ops loop (contract)

Phase 9 is the clinical-operations loop: a serious-risk crossing populates a scope-bound in-app
at-risk surface (reasons + trajectory + recommended actions) and rings a minimal PII-free email
doorbell. It is framed as a **CAPABILITY DEMONSTRATION on labeled-synthetic data — NEVER a clinical
finding.** The sequence/trajectory signal is a synthetic capability proof; every Phase-9 output
carries the synthetic-demonstration label where the signal is synthetic (see § below +
`/specs/dashboard.md § Synthetic data disclosure`). Gate order: 9.1 real `top_factors` →
9.2 crossing-detection + scope-bound at-risk `/cohort` → 9.3 recommended actions → 9.4 at-risk
frontend → 9.5 per-user notification email + scope-bound routing → 9.6 email notifier
(SMTP/Vault/egress/CI-stub) → 9.7 synthetic trajectory demo (done-when).

### Serious-risk threshold (fixed)
The **serious-risk threshold is the existing HIGH band (`risk_score > 0.6`)** — there is NO new
threshold and no new band. A **serious-risk crossing** is a champion-point transition from a
non-`high` band to `high` across a participant's champion-of-record history (the latest champion
point is `high` and the immediately-prior champion point was `medium`/`low`/absent). This reuses
the band logic already fixed in § Scoring contract; a threshold change still requires a model-card
update + version bump. As of Gate 9.7a the `> 0.6` crossing is reachable by the **real calibrated
champion** (`sequence_v1.1:demo`, see § Output calibration) on a heavy-disengagement trajectory —
no scorer override is required to drive a genuine crossing.

### Reasons / `top_factors` = REAL model attributions (fixed; 9.1 implements)
`top_factors` / `reasons` MUST be **genuine attributions from the scoring model**, never invented
narrative:
- For the **structural** model: SHAP / feature-importance over the model's actual feature matrix.
- For the **sequence (LSTM)** model: the model's actual sequence/static features (gradient or
  attention attribution over the real inputs), not a hand-authored story.
- They are honestly labeled as **model attributions**; on synthetic data they reflect the
  **planted synthetic signal** and are labeled `synthetic` (the score's `synthetic` flag already
  governs this). 9.1 replaces today's empty `top_factors=[]` / `reasons=[]` writeback (the worker
  currently writes empty lists) with real attributions, under the same champion/shadow and
  leakage discipline as the score itself.
- **FORBIDDEN:** hand-written or rule-invented clinical reasons unattached to the model's
  computation. A reason that does not trace to the model's attribution of its own inputs is a
  spec violation — it would fabricate a clinical rationale the model never produced. The
  recommended-actions layer (9.3, `/specs/dashboard.md`) is the place for protocol *guidance*; it
  is explicitly distinct from `reasons` and is never presented as a model attribution.

### Crossing detection — worker-side, idempotent (fixed; 9.2/9.5/9.6 implement)
Crossing detection runs **in the scoring worker, immediately after the score writeback** (the
worker has just appended the new champion point and can read the prior champion-of-record band, so
no separate scan service is needed). The detection is **recorded idempotently**: a retried worker
job (the writeback APPENDS and is not upsert-idempotent — see § Writeback / § Execution model)
MUST NOT produce a second crossing record or a second email for the same transition.

### Crossing dedupe (fixed)
A serious-risk crossing fires the notification **ONCE on the non-`high` → `high` transition**. It
does **NOT** re-fire on every subsequent rescore while the participant remains `high` (only a new
non-`high` → `high` transition fires again). Dedupe keys on the **transition** (a durable
per-participant crossing marker), and is idempotent so a retried job never double-fires. The
in-app `high`-crossing alert already specced in `/specs/dashboard.md § Demo loop` is the UI face of
the same transition; the email is the out-of-band doorbell for it.

### Synthetic provenance on every Phase-9 output (fixed)
The synthetic flag (`participant_score.synthetic`, already propagated end-to-end) governs every
Phase-9 surface: the at-risk list, the per-participant panel, any report, and the **email body**
all carry the synthetic-demonstration label where the signal is synthetic. The non-dismissible
banner contract from `/specs/dashboard.md § Synthetic data disclosure` extends to the at-risk
surface and the email. A Phase-9 output that renders a synthetic-derived crossing without the
disclosure is a spec violation.
