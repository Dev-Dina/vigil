// STUB DATA LAYER — Phase 3.
// Every export here is hard-coded but typed to /specs/api.md response schemas.
// These functions stand in for the real scoped API client. No real network,
// no auth, no backend. Each is the data boundary to be wired in phase 4/5.

import type {
  AssistantTurn,
  CohortRow,
  CohortSummary,
  ConversationOut,
  DriftPoint,
  InterventionIn,
  InterventionOut,
  JobAccepted,
  JobPending,
  MeOut,
  ModelStatus,
  Page,
  ParticipantDetail,
  RiskExplanation,
} from "./types"

const ISO = (d: string) => new Date(d).toISOString()

// ---- auth ----
// TODO(phase4): wire to GET /auth/me
export const STUB_ME: MeOut = {
  user_id: "usr_0a1b2c3d",
  role: "coordinator",
  home_sponsor_id: "spn_1234",
  home_cro_id: null,
  scope: [{ sponsor_id: "spn_1234", trial_id: "trl_22", site_id: "sit_07" }],
}

// ---- cohort ----
const COHORT_ROWS: CohortRow[] = [
  {
    participant_id: "PT-1001",
    trial_id: "trl_22",
    site_id: "sit_07",
    risk_score: 0.82,
    risk_band: "high",
    top_factors: ["missed_visits", "symptom_burden", "travel_distance"],
    updated_at: ISO("2026-06-04T09:15:00Z"),
  },
  {
    participant_id: "PT-1002",
    trial_id: "trl_22",
    site_id: "sit_07",
    risk_score: 0.74,
    risk_band: "high",
    top_factors: ["adverse_event", "non_compliance"],
    updated_at: ISO("2026-06-04T08:40:00Z"),
  },
  {
    participant_id: "PT-1003",
    trial_id: "trl_22",
    site_id: "sit_07",
    risk_score: 0.56,
    risk_band: "medium",
    top_factors: ["work_schedule", "side_effect_concerns"],
    updated_at: ISO("2026-06-03T14:05:00Z"),
  },
  {
    participant_id: "PT-1004",
    trial_id: "trl_22",
    site_id: "sit_07",
    risk_score: 0.45,
    risk_band: "medium",
    top_factors: ["distance_to_site"],
    updated_at: ISO("2026-06-03T11:20:00Z"),
  },
  {
    participant_id: "PT-1005",
    trial_id: "trl_22",
    site_id: "sit_07",
    risk_score: 0.31,
    risk_band: "low",
    top_factors: ["family_obligations"],
    updated_at: ISO("2026-06-02T16:42:00Z"),
  },
  {
    participant_id: "PT-1006",
    trial_id: "trl_22",
    site_id: "sit_07",
    risk_score: 0.24,
    risk_band: "low",
    top_factors: ["loss_of_interest"],
    updated_at: ISO("2026-06-02T10:30:00Z"),
  },
]

// TODO(phase4/5): wire to GET /cohort
export async function getCohort(): Promise<Page<CohortRow>> {
  return { items: COHORT_ROWS, next_cursor: null, total: COHORT_ROWS.length }
}

// TODO(phase4/5): wire to GET /cohort/summary
export async function getCohortSummary(): Promise<CohortSummary> {
  return {
    total: 847,
    by_band: { high: 12, medium: 53, low: 782 },
    mean_risk: 0.47,
  }
}

// ---- participants ----
// TODO(phase4/5): wire to GET /participants/{participant_id}
export async function getParticipant(participantId: string): Promise<ParticipantDetail> {
  return {
    participant_id: participantId,
    trial_id: "trl_22",
    site_id: "sit_07",
    status: "active",
    risk_score: 0.78,
    enrolled_at: ISO("2026-01-15T00:00:00Z"),
    identity: null, // populated ONLY for site roles; null otherwise
  }
}

// TODO(phase4/5): wire to GET /participants/{participant_id}/risk
export async function getParticipantRisk(participantId: string): Promise<RiskExplanation> {
  return {
    participant_id: participantId,
    risk_score: 0.78,
    horizon_days: 28,
    factors: [
      { feature: "missed_appointments", contribution: 0.85 },
      { feature: "symptom_burden", contribution: 0.72 },
      { feature: "travel_distance", contribution: 0.58 },
      { feature: "employment_status", contribution: 0.41 },
      { feature: "social_support", contribution: -0.28 },
    ],
    model_version: "retention-v3.2.1",
  }
}

// TODO(phase4/5): wire to POST /participants/{participant_id}/interventions
export async function logIntervention(
  participantId: string,
  body: InterventionIn,
): Promise<InterventionOut> {
  return {
    id: "iv_" + Math.random().toString(36).slice(2, 10),
    participant_id: participantId,
    kind: body.kind,
    note: body.note,
    actor_user_id: STUB_ME.user_id,
    created_at: new Date().toISOString(),
  }
}

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
