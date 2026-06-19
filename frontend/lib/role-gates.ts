// Role gate constants (dashboard.md § Role-scoped rendering).
// The API is the authoritative gate — UI suppression is cosmetic only.
// Canonical platform/ML-admin JWT string is "platform_admin" (specs/domain.md § Roles).
import type { Role } from "./types"

// Platform tier — any of these reaches the monitoring surface (the API is the authoritative gate;
// UI suppression is cosmetic). Includes the two operational roles (Gate RBAC-OPS).
export const PLATFORM_ROLES: Role[] = [
  "platform_admin",
  "auditor",
  "mlops_engineer",
  "llmops_engineer",
]

// Per-view gates (Gate RBAC-OPS) — which monitoring VIEW a platform-tier role sees. Mirrors the
// backend capability table (specs/domain.md § Operational roles). Enforcement is server-side;
// these only decide which view renders vs. the honest "not available to your role" treatment.
// MLOps view = model lifecycle (drift / registry / model traffic). LLMOps view = LLM cost + guardrail.
export const CAN_VIEW_MLOPS: Role[] = ["platform_admin", "auditor", "mlops_engineer"]
export const CAN_VIEW_LLMOPS: Role[] = ["platform_admin", "auditor", "llmops_engineer"]

export const CAN_TRIGGER_SCORING: Role[] = [
  "sponsor_oversight",
  "study_manager",
  "principal_investigator",
  "coordinator",
]

export const CAN_LOG_INTERVENTIONS: Role[] = [
  "sponsor_oversight",
  "study_manager",
  "principal_investigator",
  "coordinator",
]

export const CAN_SEE_IDENTITY: Role[] = ["principal_investigator", "coordinator"]
