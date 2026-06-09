// Role gate constants (dashboard.md § Role-scoped rendering).
// The API is the authoritative gate — UI suppression is cosmetic only.
// Canonical platform/ML-admin JWT string is "platform_admin" (specs/domain.md § Roles).
import type { Role } from "./types"

export const PLATFORM_ROLES: Role[] = ["platform_admin", "auditor"]

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
