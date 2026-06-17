// STUB DATA LAYER — Phase 3.
// Every export here is hard-coded but typed to /specs/api.md response schemas.
// These functions stand in for the real scoped API client. No real network,
// no auth, no backend. Each is the data boundary to be wired in phase 4/5.

import type {
  AssistantTurn,
  ConversationOut,
  JobAccepted,
  JobPending,
} from "./types"

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
// Monitoring is wired to the real platform/auditor backend (Gate 6.4): GET /monitoring/models,
// /drift (honest-empty), /cost, and /messages via lib/api.ts. No monitoring stub remains here.

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
