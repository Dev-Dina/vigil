# Scoring Spec

## Decisions (fixed)
- **Single feature path.** The live scoring pipeline calls the SAME `ContractTransformer` (fit
  artifact from the committed model version) and the SAME leakage assertions
  (`assert_feature_time_before_t`, `run_smoke`) as training. There is no separate scoring feature
  path. Any schema drift from the training contract is a hard error, not a silent fallback.
- **Champion model.** The sequence model (Phase 3, Step 3) is the operational scorer for the
  synthetic T2D demo. The discrete-time hazard and pan-indication baselines are evaluation
  artifacts only. Model routing (champion / challenger / shadow) is governed by the model registry
  (`/specs/infra.md`); this spec fixes the contract, not the model identity.
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

## Writeback

### `participant_score` table
Every writeback upserts into `participant_score` (unique key: `participant_id + model_version`).
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
    computed_at    timestamptz NOT NULL DEFAULT now(),
    UNIQUE (participant_id, model_version)
);
-- RLS: standard sponsor-scoped policy (same as every operational table).
-- Index on (sponsor_id, trial_id) for the cohort query hot path.
-- Index on (sponsor_id, risk_band) for band-filtered triage views.
```

Relation to API: `GET /cohort` reads `risk_score`, `risk_band`, `top_factors` from this table
(via the scoped repository). `GET /participants/{id}/risk` reads the full `reasons` list.

### Audit log on every write
Every writeback (insert or upsert) appends a row to the existing `audit_log` table:
- `action: "score_writeback"`
- `actor_id:` the Arq job id (not a human user; scores are computed)
- `resource_type: "participant_score"`, `resource_id: participant_id`
- `sponsor_id:` the scoped tenant
- `payload: {model_version, synthetic, n_rows_written}` — no PII, no risk values in the log

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
- On failure: exponential backoff + jitter (`/specs/infra.md` resilience rules). Idempotent
  (upsert on unique key); safe to retry.
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
