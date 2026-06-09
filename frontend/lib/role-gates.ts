// Role gate constants (dashboard.md § Role-scoped rendering).
// The API is the authoritative gate — UI suppression is cosmetic only.
// NOTE: domain.py uses "platform_admin" but specs/dashboard.md and types.ts use "ml_admin".
// Flagged as spec conflict: tracked in ROADMAP TODO. Frontend uses the types.ts enum values.
import type { Role } from "./types"

export const PLATFORM_ROLES: Role[] = ["ml_admin", "auditor"]

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
