// Real API client (Phase 4) — replaces stubs.ts at every data boundary.
//
// Discipline:
// - JWT is read from auth-context (memory) or localStorage; never a query param.
// - NEVER add sponsor_id as a query param. The JWT carries scope; RLS handles filtering.
// - 403 responses throw ApiError so callers can distinguish scope errors.
// - All function signatures match stubs.ts so pages swap import without changing call sites.

import type {
  AssistantTurn,
  CohortRow,
  CohortSummary,
  ConversationOut,
  CostReport,
  DriftPoint,
  InterventionIn,
  InterventionOut,
  JobAccepted,
  JobPending,
  MessageEventOut,
  ModelStatus,
  Page,
  ParticipantDetail,
  RiskExplanation,
  RiskHistory,
} from "./types"

const API_BASE =
  typeof window !== "undefined"
    ? (process.env.NEXT_PUBLIC_API_URL ?? "http://localhost:8000")
    : "http://localhost:8000"

const TOKEN_KEY = "vigil_access_token"

// ---------------------------------------------------------------------------
// Typed error
// ---------------------------------------------------------------------------

export class ApiError extends Error {
  constructor(
    public readonly status: number,
    public readonly error: string,
    public readonly detail: string,
  ) {
    super(`[${status}] ${error}: ${detail}`)
    this.name = "ApiError"
  }
}

// ---------------------------------------------------------------------------
// Internal helpers
// ---------------------------------------------------------------------------

function getToken(): string | null {
  if (typeof window === "undefined") return null
  return localStorage.getItem(TOKEN_KEY)
}

async function apiFetch<T>(
  path: string,
  init: RequestInit = {},
): Promise<T> {
  const token = getToken()
  const headers: Record<string, string> = {
    "Content-Type": "application/json",
    ...(init.headers as Record<string, string>),
  }
  if (token) {
    headers["Authorization"] = `Bearer ${token}`
  }

  const resp = await fetch(`${API_BASE}${path}`, { ...init, headers })

  if (!resp.ok) {
    let errorBody: { error?: string; detail?: string } = {}
    try {
      errorBody = await resp.json()
    } catch {
      // ignore parse failure
    }
    throw new ApiError(
      resp.status,
      errorBody.error ?? "error",
      errorBody.detail ?? resp.statusText,
    )
  }

  if (resp.status === 204) return undefined as unknown as T
  return resp.json() as Promise<T>
}

// ---------------------------------------------------------------------------
// Cohort
// ---------------------------------------------------------------------------

export async function getCohort(
  params?: {
    trial_id?: string
    site_id?: string
    risk_band?: string
    sort?: "risk_desc" | "risk_asc"
    limit?: number
  },
): Promise<Page<CohortRow>> {
  const qs = new URLSearchParams()
  if (params?.trial_id) qs.set("trial_id", params.trial_id)
  if (params?.site_id) qs.set("site_id", params.site_id)
  if (params?.risk_band) qs.set("risk_band", params.risk_band)
  if (params?.sort) qs.set("sort", params.sort)
  if (params?.limit != null) qs.set("limit", String(params.limit))
  const query = qs.toString() ? `?${qs.toString()}` : ""
  return apiFetch<Page<CohortRow>>(`/api/v1/cohort${query}`)
}

export async function getCohortSummary(
  params?: { trial_id?: string; site_id?: string },
): Promise<CohortSummary> {
  const qs = new URLSearchParams()
  if (params?.trial_id) qs.set("trial_id", params.trial_id)
  if (params?.site_id) qs.set("site_id", params.site_id)
  const query = qs.toString() ? `?${qs.toString()}` : ""
  return apiFetch<CohortSummary>(`/api/v1/cohort/summary${query}`)
}

// ---------------------------------------------------------------------------
// Participants
// ---------------------------------------------------------------------------

export async function getParticipant(participantId: string): Promise<ParticipantDetail> {
  return apiFetch<ParticipantDetail>(`/api/v1/participants/${encodeURIComponent(participantId)}`)
}

export async function getParticipantRisk(participantId: string): Promise<RiskExplanation> {
  return apiFetch<RiskExplanation>(
    `/api/v1/participants/${encodeURIComponent(participantId)}/risk`,
  )
}

export async function getParticipantRiskHistory(
  participantId: string,
): Promise<RiskHistory> {
  // Champion-at-each-point trajectory (H2b). 200 with empty points = in-scope, no history;
  // 404 = out-of-scope/not-found (ApiError). Per-point model_version preserved for the
  // version-boundary rendering — do NOT flatten away the model identity.
  return apiFetch<RiskHistory>(
    `/api/v1/participants/${encodeURIComponent(participantId)}/risk/history`,
  )
}

export async function logIntervention(
  participantId: string,
  body: InterventionIn,
): Promise<InterventionOut> {
  return apiFetch<InterventionOut>(
    `/api/v1/participants/${encodeURIComponent(participantId)}/interventions`,
    { method: "POST", body: JSON.stringify(body) },
  )
}

export async function getInterventions(
  participantId: string,
): Promise<Page<InterventionOut>> {
  return apiFetch<Page<InterventionOut>>(
    `/api/v1/participants/${encodeURIComponent(participantId)}/interventions`,
  )
}

// ---------------------------------------------------------------------------
// Monitoring
// ---------------------------------------------------------------------------

export async function getModels(): Promise<Page<ModelStatus>> {
  return apiFetch<Page<ModelStatus>>("/api/v1/monitoring/models")
}

export async function getDrift(): Promise<Page<DriftPoint>> {
  // Honest-empty read surface (6.2): items === [] until real drift computation exists.
  return apiFetch<Page<DriftPoint>>("/api/v1/monitoring/drift")
}

export interface MessageQuery {
  surface?: string
  conversation_id?: string
  role_or_guest_scope?: string
  guardrail_decision?: string
  status?: string
  since?: string
  until?: string
  limit?: number
}

export async function getMessages(
  params?: MessageQuery,
): Promise<Page<MessageEventOut>> {
  // GET /monitoring/messages — platform/auditor only (403 otherwise; the API is the real guard).
  const qs = new URLSearchParams()
  if (params?.surface) qs.set("surface", params.surface)
  if (params?.conversation_id) qs.set("conversation_id", params.conversation_id)
  if (params?.role_or_guest_scope) qs.set("role_or_guest_scope", params.role_or_guest_scope)
  if (params?.guardrail_decision) qs.set("guardrail_decision", params.guardrail_decision)
  // The 6.1 endpoint names this query param `status_filter` (avoids shadowing fastapi.status).
  if (params?.status) qs.set("status_filter", params.status)
  if (params?.since) qs.set("since", params.since)
  if (params?.until) qs.set("until", params.until)
  if (params?.limit != null) qs.set("limit", String(params.limit))
  const query = qs.toString() ? `?${qs.toString()}` : ""
  return apiFetch<Page<MessageEventOut>>(`/api/v1/monitoring/messages${query}`)
}

export interface CostQuery {
  surface?: string
  since?: string
  until?: string
}

export async function getCost(params?: CostQuery): Promise<CostReport> {
  // GET /monitoring/cost — platform/auditor only; rollups from REAL persisted usage (honest-zero
  // until a cost rate is configured), never fabricated.
  const qs = new URLSearchParams()
  if (params?.surface) qs.set("surface", params.surface)
  if (params?.since) qs.set("since", params.since)
  if (params?.until) qs.set("until", params.until)
  const query = qs.toString() ? `?${qs.toString()}` : ""
  return apiFetch<CostReport>(`/api/v1/monitoring/cost${query}`)
}

// ---------------------------------------------------------------------------
// Assistant (async 202 + poll)
// ---------------------------------------------------------------------------

export async function openConversation(): Promise<ConversationOut> {
  return apiFetch<ConversationOut>("/api/v1/assistant/conversations", { method: "POST" })
}

export async function postMessage(
  conversationId: string,
  body: { content: string; participant_id?: string | null },
): Promise<JobAccepted> {
  return apiFetch<JobAccepted>(
    `/api/v1/assistant/conversations/${encodeURIComponent(conversationId)}/messages`,
    { method: "POST", body: JSON.stringify(body) },
  )
}

export async function pollJob(
  jobId: string,
  _conversationId: string,
): Promise<AssistantTurn | JobPending> {
  return apiFetch<AssistantTurn | JobPending>(
    `/api/v1/assistant/jobs/${encodeURIComponent(jobId)}`,
  )
}

// ---------------------------------------------------------------------------
// Demo loop — inject events + poll scoring job
// ---------------------------------------------------------------------------

export interface InjectEventsIn {
  trial_id: string
  events: unknown[]
  demo_mode_key: string
}

export async function injectEvents(body: InjectEventsIn): Promise<void> {
  return apiFetch<void>("/api/v1/scoring/inject_events", {
    method: "POST",
    body: JSON.stringify(body),
  })
}

export interface ScoringJobStatus {
  job_id: string
  trial_id: string
  status: "queued" | "running" | "done" | "failed"
  n_scored?: number
}

export async function getScoringJob(jobId: string): Promise<ScoringJobStatus> {
  return apiFetch<ScoringJobStatus>(`/api/v1/scoring/jobs/${encodeURIComponent(jobId)}`)
}
