// API response/request types — mirror /specs/api.md exactly.
// These are the ONLY shapes the UI should consume. No invented fields.
// Where api.md references a type without defining its fields, it is noted inline.

// ---- shared envelopes (core/schemas.py) ----
export interface Page<T> {
  items: T[]
  next_cursor: string | null
  total: number
}

export interface ErrorOut {
  error: string
  detail: string
  request_id: string
}

// ---- auth (/auth) ----
// Seven canonical JWT role strings — /specs/domain.md § Roles.
// Canonical platform/ML-admin string is "platform_admin" (matches domain.py).
export type Role =
  | "study_manager"
  | "cra"
  | "sponsor_oversight"
  | "principal_investigator"
  | "coordinator"
  | "platform_admin"
  | "auditor"

export interface ScopeTuple {
  sponsor_id: string
  trial_id: string | null
  site_id: string | null
}

export interface LoginIn {
  email: string
  password: string
}

export interface TokenOut {
  access_token: string
  token_type: "bearer"
  expires_in: number // seconds
}

export interface MeOut {
  user_id: string
  role: Role
  home_sponsor_id: string | null
  home_cro_id: string | null
  scope: ScopeTuple[]
}

// ---- cohort (/cohort) ----
export type RiskBand = "high" | "medium" | "low"

export interface CohortRow {
  participant_id: string // coded id, never identifiable
  trial_id: string
  site_id: string
  risk_score: number // 0..1
  risk_band: RiskBand
  top_factors: string[] // explanation tags
  updated_at: string // ISO datetime
  synthetic: boolean // from participant_score.synthetic; always surfaced
}

export interface CohortSummary {
  total: number
  by_band: Record<RiskBand, number>
  mean_risk: number
}

// ---- participants (/participants) ----
export type ParticipantStatus = "active" | "completed" | "withdrawn" | "censored"

// api.md: identity populated ONLY for site roles; null otherwise. The detailed
// shape of ParticipantIdentity is owned by the backend (PII) — kept opaque here.
export interface ParticipantIdentity {
  [key: string]: unknown
}

export interface ParticipantDetail {
  participant_id: string
  trial_id: string
  site_id: string
  status: ParticipantStatus
  risk_score: number // 0..1
  enrolled_at: string // ISO datetime
  synthetic: boolean // from participant_score.synthetic; always surfaced
  identity: ParticipantIdentity | null
}

// api.md references FactorContribution ("signed feature contributions, sorted by
// |impact|") without a field list. Shape below is inferred; confirm at wire time.
export interface FactorContribution {
  feature: string
  contribution: number // signed; sign encodes direction
}

export interface RiskExplanation {
  participant_id: string
  risk_score: number
  horizon_days: number // default 28
  factors: FactorContribution[]
  model_version: string
}

// Champion risk trajectory (H2b semantic b) — champion-at-each-point, cross-version.
// Each point carries its own model_version so a promotion boundary stays visible.
export interface RiskHistoryPoint {
  risk_score: number
  risk_band: RiskBand
  model_version: string // champion-of-record model that produced THIS point
  model_card_ref: string
  synthetic: boolean // per-point provenance; never smoothed away
  computed_at: string // ISO datetime
}

export interface RiskHistory {
  participant_id: string
  points: RiskHistoryPoint[] // ordered by computed_at ASC; may be empty (in-scope, no history)
}

export type InterventionKind = "call" | "visit_reschedule" | "reminder" | "note"

export interface InterventionIn {
  kind: InterventionKind
  note: string // max 2000
}

export interface InterventionOut {
  id: string
  participant_id: string
  kind: string
  note: string
  actor_user_id: string
  created_at: string
}

// ---- assistant (/assistant) — async 202 + poll ----
export interface ConversationOut {
  conversation_id: string
}

export interface AssistantMessageIn {
  content: string // max 8000
  participant_id?: string | null // if set, must be within scope
}

export interface JobAccepted {
  job_id: string
  conversation_id: string
  status: "queued"
}

export interface AssistantTurn {
  conversation_id: string
  role: "user" | "assistant"
  content: string // redacted at rest
  guardrail_decision: "allowed" | "blocked"
  created_at: string
}

// Poll response is AssistantTurn | JobPending; JobPending shape not given in
// api.md — modeled minimally so the poll loop can branch on it.
export interface JobPending {
  job_id: string
  status: "queued" | "running"
}

// ---- monitoring (/monitoring) ----
export interface ModelStatus {
  model_name: string
  version: string
  role: "champion" | "challenger" | "shadow"
  regime: string
  health: "healthy" | "degraded" | "fallback"
  promoted_at: string | null
}

export interface DriftPoint {
  model_name: string
  metric: string
  value: number
  threshold: number
  breached: boolean
  ts: string
}

export interface MessageEventOut {
  conversation_id: string
  request_id: string
  role_or_guest_scope: string
  guardrail_decision: "allowed" | "blocked"
  llm_provider_model: string
  latency_ms: number
  status: string
  ts: string
}
