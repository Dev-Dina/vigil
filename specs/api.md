# API Spec

## Decisions (fixed)
- FastAPI, versioned under `/api/v1`, one `APIRouter` per domain.
- Auth/scope is a dependency injected into every protected route.

## JWT claim shape
Signed with the Vault-held key; verified on every request by the auth dependency, which resolves
it into the request-scoped `Scope` object that sets the RLS session variables. The client never
asserts scope — it comes only from these verified claims.

```jsonc
{
  // --- registered ---
  "iss": "vigil-auth",            // issuer
  "sub": "usr_0a1b2c3d",          // user id (stable, opaque)
  "iat": 1733000000,              // issued-at (epoch s)
  "exp": 1733001800,              // expiry — short (≤ 30 min); refresh via Redis session
  "jti": "sess_9f8e7d",           // session id; key into the Redis revocation store
  // --- vigil scope (the authorization payload) ---
  "role": "study_manager",        // one of the seven roles (snake_case enum)
  "home_sponsor_id": "spn_1234",  // null for CRO/platform users
  "home_cro_id": null,            // set for CRO-side users; else null
  "scope": [                      // resolved (sponsor, trial?, site?) tuples; [] for platform
    { "sponsor_id": "spn_1234", "trial_id": null, "site_id": null },
    { "sponsor_id": "spn_5678", "trial_id": "trl_22", "site_id": "sit_07" }
  ],
  "scope_ver": 3                  // bumped on any grant change → forces re-login / token invalidation
}
```

Rules: `role` ∈ the seven canonical JWT strings defined in `/specs/domain.md § Roles`
(`study_manager`, `cra`, `sponsor_oversight`, `principal_investigator`, `coordinator`,
`platform_admin`, `auditor`). Exactly one of `home_sponsor_id` /
`home_cro_id` is set, or neither for platform (ML admin, auditor). `scope` is the union of the
user's `assignment_grant` tuples (CRO) or the single fixed tuple (sponsor/site user); a `null`
`trial_id`/`site_id` widens to all under the parent. Platform users carry `scope: []` and reach
only platform/global tables. The session (`jti`) lives in Redis and is revocable; a revoked or
stale-`scope_ver` token is rejected even before expiry.

## Endpoints
All routes are under `/api/v1`, one `APIRouter` per domain, and **every** protected route takes
the auth/scope dependency (`scope: Scope = Depends(require_scope)`) — no handler reaches a
repository without a resolved scope, and repositories run only in the tenant-scoped session that
scope opens. Shared envelope types below are reused across domains.

```python
# core response/pagination envelopes (core/schemas.py)
class Page(BaseModel):                      # generic cursor pagination
    items: list[Any]
    next_cursor: str | None = None
    total: int

class ErrorOut(BaseModel):                  # uniform error body
    error: str                              # machine code, e.g. "scope_denied"
    detail: str
    request_id: str
```

### auth (`/auth`) — unauthenticated entry + session lifecycle
| Method · Path | Request | Response | Notes |
|---|---|---|---|
| `POST /auth/login` | `LoginIn` | `TokenOut` | resolves scope, mints JWT, opens Redis session |
| `POST /auth/refresh` | `RefreshIn` | `TokenOut` | rotates token if Redis session live + `scope_ver` current |
| `POST /auth/logout` | — (bearer) | `204` | revokes `jti` in Redis |
| `GET /auth/me` | — (bearer) | `MeOut` | echoes resolved identity + scope (no secrets) |

```python
class LoginIn(BaseModel):
    email: EmailStr
    password: SecretStr
class RefreshIn(BaseModel):
    refresh_token: SecretStr
class TokenOut(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int                         # seconds
class MeOut(BaseModel):
    user_id: str
    role: Role
    home_sponsor_id: str | None
    home_cro_id: str | None
    scope: list[ScopeTuple]
```

### me (`/me`) — per-user notification settings (Phase 9)
A user manages their OWN notification preferences. Used by the Phase-9 clinical-ops loop to route
the PII-free serious-risk doorbell email to the user's chosen address.
| Method · Path | Request | Response | Notes |
|---|---|---|---|
| `GET /me/notification-email` | — (bearer) | `NotificationEmailOut` | the caller's own notification email (null if unset) |
| `PUT /me/notification-email` | `NotificationEmailIn` | `NotificationEmailOut` | set/update/clear the CALLER's OWN notification email; audited |

```python
class NotificationEmailIn(BaseModel):
    notification_email: EmailStr | None        # None clears it; defaults UNSET per user
class NotificationEmailOut(BaseModel):
    user_id: str
    notification_email: EmailStr | None
```

- A user may set ONLY their own `notification_email` (the field is on the `user` record,
  `/specs/domain.md`); there is no admin path to set another user's address here. Every change is
  audited.
- The address is used solely as a **recipient** of the PII-free doorbell email; it is NEVER a
  secret and is read from the user record at routing time — never from Vault, never a code
  constant (`/specs/isolation.md § Phase 9`, `/specs/infra.md § Phase 9 notification egress`).
- **Recipient resolution is scope-bound (sacred):** a serious-risk crossing routes only to the
  notification emails of users whose scope COVERS the participant's site
  (`/specs/domain.md § Notification routing (Phase 9)`, `/specs/isolation.md § Phase 9`).

### cohort (`/cohort`) — ranked triage list (scope-filtered, coded data only)
| Method · Path | Request | Response | Notes |
|---|---|---|---|
| `GET /cohort` | `CohortQuery` (query params) | `Page[CohortRow]` | RLS-filtered to caller's sponsors/trials/sites; ranked by risk |
| `GET /cohort/summary` | `CohortQuery` | `CohortSummary` | counts/risk bands for the same scoped slice |

```python
class CohortQuery(BaseModel):
    trial_id: str | None = None             # must be within scope; else 403 scope_denied
    site_id: str | None = None
    risk_band: Literal["high", "medium", "low"] | None = None
    sort: Literal["risk_desc", "risk_asc"] = "risk_desc"
    cursor: str | None = None
    limit: int = Field(50, le=200)
class CohortRow(BaseModel):
    participant_id: str                     # coded id, never identifiable
    trial_id: str
    site_id: str
    risk_score: float = Field(ge=0, le=1)
    risk_band: Literal["high", "medium", "low"]
    top_factors: list[str]                  # explanation tags
    updated_at: datetime
    enrolled_at: datetime                   # trial enrollment start; drives "days enrolled"
    synthetic: bool                         # from participant_score.synthetic; always surfaced
class CohortSummary(BaseModel):
    total: int
    by_band: dict[Literal["high", "medium", "low"], int]
    mean_risk: float
```

**Phase 9 — at-risk view (no new endpoint).** The Phase-9 at-risk surface is
`GET /cohort?risk_band=high&sort=risk_desc`. `CohortQuery` already declares `risk_band` and `sort`;
the cohort service currently ignores them and 9.2 wires the `risk_band` filter + honors
`sort=risk_desc` server-side (scope-bound exactly as today via RLS + `scope_filter`). See
`/specs/dashboard.md § At-risk surface (Phase 9)`.

### participants (`/participants`) — detail, factors, interventions
| Method · Path | Request | Response | Notes |
|---|---|---|---|
| `GET /participants/{participant_id}` | path | `ParticipantDetail` | 403 if id outside scope; identities only for site roles (PI/CRC) |
| `GET /participants/{participant_id}/risk` | path | `RiskExplanation` | per-feature contributions behind the flag; champion-only (`model_version` is always the champion — shadow/challenger rows are filtered out per routing.md § (i)); `404` if no champion score (fail-closed, never a non-champion fallback) |
| `GET /participants/{participant_id}/risk/history` | path | `RiskHistory` | champion risk **trajectory** over time (H1 appended history), ordered by `computed_at` ASC. **Semantic (b) — champion-at-each-point:** each point is the row whose `model_version` was the CHAMPION-OF-RECORD at its `computed_at` (reconstructed from the B3 promotion/fallback timeline); the trajectory spans version changes and each point carries its real `model_version`/`model_card_ref`/`synthetic` (B4) — a cross-version trajectory is honestly labeled, never smoothed. Shadow/challenger rows, and rows outside their version's champion tenure, NEVER appear. **Visibility vs emptiness:** out-of-scope / not-found (RLS-hidden) → `404`; platform role → `403`; an in-scope participant with no champion points yet → `200` with `points: []` (a normal data state, not an error). |
| `POST /participants/{participant_id}/interventions` | `InterventionIn` | `InterventionOut` | logs a triage action; audited |
| `GET /participants/{participant_id}/interventions` | path | `Page[InterventionOut]` | history for this participant |

```python
class ParticipantDetail(BaseModel):
    participant_id: str
    trial_id: str
    site_id: str
    status: Literal["active", "completed", "withdrawn", "censored"]
    risk_score: float = Field(ge=0, le=1)
    enrolled_at: datetime
    synthetic: bool                               # from participant_score.synthetic; always surfaced
    identity: ParticipantIdentity | None = None   # populated ONLY for site roles; null otherwise
class RiskExplanation(BaseModel):
    participant_id: str
    risk_score: float
    horizon_days: int = 28
    factors: list[FactorContribution]       # signed feature contributions, sorted by |impact|
    model_version: str
    recommended_actions: list[SuggestedAction] = []  # Phase-9: operational coordinator next-steps
    synthetic: bool = True                  # actions labelled synthetic where the risk is synthetic
class SuggestedAction(BaseModel):           # operational next-step, NOT clinical advice (Gate 9.3)
    action: str                             # from the approved operational catalog only
    intervention_kind: str                  # pre-fills the audited POST /interventions (call|...)
    factor: str | None = None               # the attribution driver it responds to (null=baseline)
class RiskHistoryPoint(BaseModel):
    risk_score: float
    risk_band: Literal["high", "medium", "low"]
    model_version: str                      # champion-of-record model that produced THIS point
    model_card_ref: str                      # provenance — the card for THIS point's model
    synthetic: bool                          # provenance per point (B4) — never smoothed away
    computed_at: datetime
class RiskHistory(BaseModel):
    participant_id: str
    points: list[RiskHistoryPoint]          # champion-at-each-point, ordered by computed_at ASC
class InterventionIn(BaseModel):
    kind: Literal["call", "visit_reschedule", "reminder", "note"]
    note: str = Field(max_length=2000)
class InterventionOut(BaseModel):
    id: str
    participant_id: str
    kind: str
    note: str
    actor_user_id: str
    created_at: datetime
```

### assistant (`/assistant`) — the REAL-app authenticated chatbot (NOT the public Guide)
Distinct from the isolated public Guide (`/specs/isolation.md`); this assistant is scope-bound and
may explain a flag for participants within the caller's scope. Slow LLM/agent work is enqueued
(Arq), so the call returns a handle and the answer streams / is polled. Every message writes a
redacted `message_events` row (`/specs/observability.md`).
| Method · Path | Request | Response | Notes |
|---|---|---|---|
| `POST /assistant/conversations` | — | `ConversationOut` | opens a `conversation_id` |
| `POST /assistant/conversations/{conversation_id}/messages` | `AssistantMessageIn` | `JobAccepted` (`202`) | enqueues; never inline |
| `GET /assistant/conversations/{conversation_id}/messages` | path | `Page[AssistantTurn]` | redacted transcript |
| `GET /assistant/jobs/{job_id}` | path | `AssistantTurn \| JobPending` | poll for the enqueued answer |

```python
class AssistantMessageIn(BaseModel):
    content: str = Field(max_length=8000)
    participant_id: str | None = None       # if set, must be within scope; grounds the answer
class JobAccepted(BaseModel):
    job_id: str
    conversation_id: str
    status: Literal["queued"] = "queued"
class AssistantTurn(BaseModel):
    conversation_id: str
    role: Literal["user", "assistant"]
    content: str                            # redacted at rest
    guardrail_decision: Literal["allowed", "blocked"]
    created_at: datetime
```

### monitoring (`/monitoring`) — model health, drift, cost (platform/auditor scope)
All `/monitoring/*` reads are **platform/auditor only** (`403` for sponsor/site roles).
| Method · Path | Request | Response | Notes |
|---|---|---|---|
| `GET /monitoring/models` | — | `Page[ModelStatus]` | champion/challenger, version, regime (from `routing_state`) |
| `GET /monitoring/drift` | `DriftQuery` | `Page[DriftPoint]` | **REAL** computed PSI/KS drift points (Gate M1 producer → `drift_metric`); empty when none computed yet, **never fabricated**. Each point carries provenance: `synthetic` (synthetic cohort) + `constructed_demo` (a deliberately-shifted demonstration, NOT observed drift). PLATFORM/AUDITOR only (`/specs/observability.md` § Drift detection (Gate M1)). |
| `POST /monitoring/drift/run` | `DriftRunIn` | `DriftRunOut` (202) | trigger the drift producer job, **platform_admin only** (403 otherwise — auditor included; triggering a job is an action). `demo_shift != 0` enqueues a CONSTRUCTED breach demonstration (labelled), never observed drift. Also runs on a cron schedule. |
| `GET /monitoring/cost` | `CostQuery` | `CostReport` | token/cost rollups from **real** persisted `latency_ms`/`token_cost_estimate` only — honest-zero/absent if not captured, never faked (`/specs/observability.md` § Phase 6 contracts). |
| `GET /monitoring/messages` | `MessageQuery` | `Page[MessageEventOut]` | redacted `message_events` (admin observability page). **PLATFORM/AUDITOR ONLY** (403 for sponsor/site roles); runs under `scoped_session` (RLS-bound, never widens the cross-tenant-by-role boundary); **redacted fields only** — no raw column exists (`/specs/observability.md` § Inspect endpoint scope contract). |
| `POST /monitoring/models/promote` | `ModelPromoteIn` | `ModelPromoteOut` | manual champion promotion, **platform_admin only** (403 otherwise); audited (`model_promote`, non-null actor); honesty-hooked `eval_provenance` (synthetic → `architecture_validation`); non-null `model_card_ref` (specs/routing.md § Audited promotion) |

```python
class ModelStatus(BaseModel):
    model_name: str
    version: str
    role: Literal["champion", "challenger", "shadow"]
    regime: str
    health: Literal["healthy", "degraded", "fallback"]
    promoted_at: datetime | None
class DriftPoint(BaseModel):                 # Gate M1: a REAL computed PSI/KS point
    model_name: str                         # the champion model_version compared
    distribution: str                       # what drifted, e.g. "champion_risk_score"
    metric: Literal["psi", "ks"]
    value: float
    threshold: float
    breached: bool                          # breached ⇔ value > threshold
    reference_n: int
    current_n: int
    synthetic: bool                         # provenance: synthetic cohort
    constructed_demo: bool                  # current window was a constructed shift (NOT observed)
    note: str
    ts: datetime
class DriftRunIn(BaseModel):
    regime: str = "t2d"
    demo_shift: float = 0.0                  # != 0 → CONSTRUCTED breach demonstration (labelled)
class DriftRunOut(BaseModel):
    job_id: str
    regime: str
    demo_shift: float
    constructed_demo: bool
# DriftQuery: optional filters (model_name | since/until). /monitoring/drift returns the most
# recent computed Page[DriftPoint] (empty until a run computes points — never fabricated).
class CostQuery(BaseModel):                 # /monitoring/cost filters
    surface: Literal["local_assistant", "public_guide"] | None = None
    since: datetime | None = None           # ts range (UTC)
    until: datetime | None = None
class CostRollup(BaseModel):                # one (surface, model) bucket
    surface: str
    llm_provider_model: str
    turns: int
    total_cost: float                       # summed REAL token_cost_estimate; honest-zero, never faked
    total_latency_ms: int
    avg_latency_ms: float
class CostReport(BaseModel):                # rollups from REAL persisted usage only
    total_turns: int
    total_cost: float
    rollups: list[CostRollup]
class MessageEventOut(BaseModel):           # mirrors /specs/observability.md, ALREADY redacted
    id: str
    conversation_id: str
    request_id: str
    sponsor_id: str | None                  # null = Guide/platform turn (cross-tenant-by-role)
    role_or_guest_scope: str
    surface: Literal["local_assistant", "public_guide"]
    route_or_agent: str
    guardrail_decision: Literal["allowed", "blocked"]
    status: str
    llm_provider_model: str
    latency_ms: int
    token_cost_estimate: float
    retrieved_chunks: list[dict]            # citation refs only (debug-retrieval duty)
    redacted_user_msg: str                  # redacted at rest; NO raw column exists
    redacted_assistant_msg: str
    ts: datetime
# MessageQuery: optional filters surface | conversation_id | role_or_guest_scope |
# guardrail_decision | status_filter | since/until (ts range) + limit. Inspect is platform/auditor-only.
```

### admin (`/admin`) — users, assignment grants, sponsor/CRO setup
Scoped administration: every write obeys the subset rule and is audited (`/specs/domain.md`).
| Method · Path | Request | Response | Notes |
|---|---|---|---|
| `POST /admin/users` | `UserCreateIn` | `UserOut` | see scoped-creation note below |
| `GET /admin/users` | `UserQuery` | `Page[UserOut]` | only users within the caller's scope |
| `POST /admin/users/{user_id}/grants` | `GrantIn` | `GrantOut` | adds an `assignment_grant`; subset-checked; bumps `scope_ver` |
| `DELETE /admin/users/{user_id}/grants/{grant_id}` | path | `204` | revokes a grant; audited; bumps `scope_ver` |
| `POST /admin/sponsors` | `SponsorCreateIn` | `SponsorOut` | platform admin only (tenant root + first admin) |

```python
class UserCreateIn(BaseModel):
    email: EmailStr
    role: Role
    home_sponsor_id: str | None = None      # forced to creator's sponsor for sponsor-side admins
    home_cro_id: str | None = None
    initial_grants: list[GrantIn] = []      # each must be a subset of the creator's scope
class UserOut(BaseModel):
    user_id: str
    email: EmailStr
    role: Role
    home_sponsor_id: str | None
    home_cro_id: str | None
    created_at: datetime
class GrantIn(BaseModel):
    sponsor_id: str
    trial_id: str | None = None
    site_id: str | None = None
class GrantOut(GrantIn):
    grant_id: str
    granted_by: str
    granted_at: datetime
```

- User management (`POST /admin/users`, assignment/scope changes) is tenant-scoped and
  RLS-protected: the created user is bound to the creator's sponsor, and the requested scope must
  be a subset of the creator's. A Sponsor A admin cannot create or assign a user in Sponsor B.
  Every such action is written to the audit trail.