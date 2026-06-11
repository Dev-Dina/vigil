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
    synthetic: bool                         # from participant_score.synthetic; always surfaced
class CohortSummary(BaseModel):
    total: int
    by_band: dict[Literal["high", "medium", "low"], int]
    mean_risk: float
```

### participants (`/participants`) — detail, factors, interventions
| Method · Path | Request | Response | Notes |
|---|---|---|---|
| `GET /participants/{participant_id}` | path | `ParticipantDetail` | 403 if id outside scope; identities only for site roles (PI/CRC) |
| `GET /participants/{participant_id}/risk` | path | `RiskExplanation` | per-feature contributions behind the flag; champion-only (`model_version` is always the champion — shadow/challenger rows are filtered out per routing.md § (i)); `404` if no champion score (fail-closed, never a non-champion fallback) |
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
| Method · Path | Request | Response | Notes |
|---|---|---|---|
| `GET /monitoring/models` | — | `Page[ModelStatus]` | champion/challenger, version, regime |
| `GET /monitoring/drift` | `DriftQuery` | `Page[DriftPoint]` | drift signals over time |
| `GET /monitoring/cost` | `CostQuery` | `CostReport` | token/cost rollups |
| `GET /monitoring/messages` | `MessageQuery` | `Page[MessageEventOut]` | redacted `message_events` (admin observability page) |

```python
class ModelStatus(BaseModel):
    model_name: str
    version: str
    role: Literal["champion", "challenger", "shadow"]
    regime: str
    health: Literal["healthy", "degraded", "fallback"]
    promoted_at: datetime | None
class DriftPoint(BaseModel):
    model_name: str
    metric: str
    value: float
    threshold: float
    breached: bool
    ts: datetime
class MessageEventOut(BaseModel):           # mirrors /specs/observability.md, already redacted
    conversation_id: str
    request_id: str
    role_or_guest_scope: str
    guardrail_decision: Literal["allowed", "blocked"]
    llm_provider_model: str
    latency_ms: int
    status: str
    ts: datetime
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