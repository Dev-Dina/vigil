// STUB DATA LAYER — Phase 3.
// Every export here is hard-coded but typed to /specs/api.md response schemas.
// These functions stand in for the real scoped API client. No real network,
// no auth, no backend. Each is the data boundary to be wired in phase 4/5.

import type {
  AssistantTurn,
  ConversationOut,
  DriftPoint,
  JobAccepted,
  JobPending,
  ModelStatus,
  Page,
} from "./types"

const ISO = (d: string) => new Date(d).toISOString()

// ---- auth ----
// Auth is wired to the real backend (Wire-1): POST /auth/login + GET /auth/me +
// POST /auth/logout via lib/auth-context.tsx. No auth stub remains here. Cohort,
// participant, monitoring, and assistant stubs below stay until Wire-2/3.

// ---- cohort ----
// Cohort is wired to the real scoped backend (Wire-2): GET /cohort + GET /cohort/summary
// via lib/api.ts. No cohort stub remains here. Participant detail + /risk + /risk/history +
// interventions are wired to the real scoped backend (Wire-3) via lib/api.ts — no participant
// or intervention stub remains here. Monitoring/assistant stubs below stay until later gates.

// ---- monitoring ----
// TODO(phase4/5): wire to GET /monitoring/models
export async function getModels(): Promise<Page<ModelStatus>> {
  const items: ModelStatus[] = [
    {
      model_name: "retention-xgb",
      version: "v3.2.1",
      role: "champion",
      regime: "phase_iii_cardio",
      health: "healthy",
      promoted_at: ISO("2026-05-12T00:00:00Z"),
    },
    {
      model_name: "retention-tabnet",
      version: "v0.9.4",
      role: "challenger",
      regime: "phase_iii_cardio",
      health: "degraded",
      promoted_at: null,
    },
    {
      model_name: "retention-lstm",
      version: "v1.1.0",
      role: "shadow",
      regime: "phase_ii_onco",
      health: "fallback",
      promoted_at: null,
    },
  ]
  return { items, next_cursor: null, total: items.length }
}

// TODO(phase4/5): wire to GET /monitoring/drift
export async function getDrift(): Promise<Page<DriftPoint>> {
  const items: DriftPoint[] = [
    {
      model_name: "retention-xgb",
      metric: "psi_risk_score",
      value: 0.12,
      threshold: 0.2,
      breached: false,
      ts: ISO("2026-06-04T00:00:00Z"),
    },
    {
      model_name: "retention-xgb",
      metric: "auc_rolling_7d",
      value: 0.71,
      threshold: 0.75,
      breached: true,
      ts: ISO("2026-06-04T00:00:00Z"),
    },
    {
      model_name: "retention-tabnet",
      metric: "psi_feature_adherence",
      value: 0.27,
      threshold: 0.2,
      breached: true,
      ts: ISO("2026-06-04T00:00:00Z"),
    },
    {
      model_name: "retention-lstm",
      metric: "prediction_drift",
      value: 0.08,
      threshold: 0.15,
      breached: false,
      ts: ISO("2026-06-04T00:00:00Z"),
    },
  ]
  return { items, next_cursor: null, total: items.length }
}

// ---- assistant (async 202 + poll) ----
// TODO(phase4/5): wire to POST /assistant/conversations
export async function openConversation(): Promise<ConversationOut> {
  return { conversation_id: "conv_" + Math.random().toString(36).slice(2, 10) }
}

// TODO(phase4/5): wire to POST /assistant/conversations/{conversation_id}/messages (202)
export async function postMessage(
  conversationId: string,
  // eslint-disable-next-line @typescript-eslint/no-unused-vars
  _body: { content: string; participant_id?: string | null },
): Promise<JobAccepted> {
  return {
    job_id: "job_" + Math.random().toString(36).slice(2, 10),
    conversation_id: conversationId,
    status: "queued",
  }
}

// TODO(phase4/5): wire to GET /assistant/jobs/{job_id} (poll until AssistantTurn)
// Stub: returns JobPending on the first poll, then an AssistantTurn after a beat.
const _jobPolls = new Map<string, number>()
export async function pollJob(
  jobId: string,
  conversationId: string,
): Promise<AssistantTurn | JobPending> {
  const seen = (_jobPolls.get(jobId) ?? 0) + 1
  _jobPolls.set(jobId, seen)
  if (seen < 2) {
    return { job_id: jobId, status: "running" }
  }
  _jobPolls.delete(jobId)
  return {
    conversation_id: conversationId,
    role: "assistant",
    content:
      "Participant PT-1001 has a risk score of 0.82 (high band). Primary drivers: missed visits, symptom burden, and travel distance. Recommend proactive outreach.",
    guardrail_decision: "allowed",
    created_at: new Date().toISOString(),
  }
}
