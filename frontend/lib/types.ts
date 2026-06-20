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
// Canonical JWT role strings — /specs/domain.md § Roles. The first seven are the data-scope
// roles; the last two are operational platform-tier roles (Gate RBAC-OPS) — platform-tier, no
// sponsor scope, action-separated. Canonical platform/ML-admin string is "platform_admin".
export type Role =
  | "study_manager"
  | "cra"
  | "sponsor_oversight"
  | "principal_investigator"
  | "coordinator"
  | "platform_admin"
  | "auditor"
  | "mlops_engineer"
  | "llmops_engineer"

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
  enrolled_at: string // ISO datetime; trial enrollment start (drives "days enrolled")
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

// Operational coordinator next-step (Gate 9.3) — NOT clinical advice. A suggestion only:
// acting goes through the audited POST /interventions; intervention_kind pre-fills that log.
export interface SuggestedAction {
  action: string
  intervention_kind: InterventionKind | string
  factor: string | null // the attribution driver it responds to (null = baseline)
}

export interface RiskExplanation {
  participant_id: string
  risk_score: number
  horizon_days: number // default 28
  factors: FactorContribution[]
  model_version: string
  recommended_actions: SuggestedAction[] // Phase-9: operational next-steps (suggestions only)
  synthetic: boolean // actions labelled synthetic where the risk is synthetic
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
  // Routed agent (retention/report/operations) that handled an answered turn; null for a user turn
  // or a guardrail/router refusal (no agent dispatched). Mirrors message_events.route_or_agent.
  agent?: string | null
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

// Real computed PSI/KS drift point (Gate M1) — mirrors monitoring.DriftPointOut exactly.
// Carries provenance: `synthetic` (synthetic cohort) and `constructed_demo` (the current
// window was a constructed shift demonstration, NOT observed drift). Surface both honestly.
export interface DriftPoint {
  model_name: string
  distribution: string
  metric: string // 'psi' | 'ks'
  value: number
  threshold: number
  breached: boolean
  reference_n: number
  current_n: number
  synthetic: boolean
  constructed_demo: boolean
  note: string
  ts: string
}

// Citation ref carried in retrieved_chunks (specs/rag.md) — already coded, no PII.
export interface Citation {
  source_type?: string
  source_id?: string
  locator?: string
  [key: string]: unknown
}

// Full 6.1 MessageEventOut shape (api.md § monitoring) — ALREADY redacted; no raw column exists.
export interface MessageEventOut {
  id: string
  conversation_id: string
  request_id: string
  sponsor_id: string | null // null = Guide/platform turn (cross-tenant-by-role)
  role_or_guest_scope: string
  surface: "local_assistant" | "public_guide"
  route_or_agent: string
  guardrail_decision: "allowed" | "blocked"
  status: string
  llm_provider_model: string
  latency_ms: number
  token_cost_estimate: number
  retrieved_chunks: Citation[] // debug-retrieval duty
  redacted_user_msg: string // redacted at rest; NO raw column exists
  redacted_assistant_msg: string
  ts: string
}

// 6.2 cost rollups — from REAL persisted usage only (honest-zero, never fabricated).
export interface CostRollup {
  surface: string
  llm_provider_model: string
  turns: number
  total_cost: number
  total_latency_ms: number
  avg_latency_ms: number
  // Gate COST-1: REAL summed token counts per (surface, model) from message_events.
  prompt_tokens: number
  completion_tokens: number
  total_tokens: number
}

export interface CostReport {
  total_turns: number
  total_cost: number
  // Gate COST-1: REAL aggregate token usage across all rollups (honest-zero, never fabricated).
  total_prompt_tokens: number
  total_completion_tokens: number
  total_tokens: number
  rollups: CostRollup[]
}

// ---- model registry (Gate M3, GET /monitoring/registry) ----
// Registered, OFFLINE-validated model versions — the PLATFORM/AUDITOR read surface (the
// informed-decision view before a governed promotion). Mirrors routing_service.RegistrationView.
// Registration enters challenger/shadow ONLY; promotion to champion is a separate audited action.
export interface RegistryEntry {
  regime: string
  model_version: string
  status: string // 'challenger' | 'shadow' | 'champion' | 'retired'
  artifact_ref: string
  model_card_ref: string
  metrics: Record<string, unknown> // supplied OFFLINE validation metrics (recorded, never computed)
  provenance: string // e.g. 'architecture_validation' | 'demonstration' — never a clinical claim
  notes: string
  registered_by: string | null
  registered_at: string // ISO datetime
}
