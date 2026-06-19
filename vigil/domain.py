"""Domain enums and the canonical role set (domain.md).

Pure value objects — no I/O, no DB, no framework. Imported by every layer so the roles and
their tenancy semantics are defined in exactly one place.

Two orthogonal axes live here:
- **Data-scope roles** (the seven tenancy roles) decide which TENANT rows a caller may reach,
  enforced by Postgres RLS keyed on ``sponsor_id``.
- **Operational platform-tier roles** (``mlops_engineer`` / ``llmops_engineer``, Gate RBAC-OPS)
  add ACTION separation INSIDE the platform tier. They carry NO sponsor scope (RLS fails closed
  for them on tenant data, exactly like ``platform_admin`` / ``auditor``); the only thing they
  vary is which platform ACTIONS/READS they may perform — see :class:`Capability` / :func:`can_do`.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    """Canonical roles. snake_case enum values match the JWT ``role`` claim (api.md).

    The first seven are the data-scope roles (domain.md § Roles). The last two are operational
    platform-tier roles (Gate RBAC-OPS) — platform-tier, NO sponsor scope, separated only by
    operational :class:`Capability`.
    """

    STUDY_MANAGER = "study_manager"  # CRO: assigned sponsors & trials
    CRA = "cra"  # CRO: assigned sites within trials
    SPONSOR_OVERSIGHT = "sponsor_oversight"  # Sponsor: own sponsor, all trials
    PRINCIPAL_INVESTIGATOR = "principal_investigator"  # Site: own site & trial
    COORDINATOR = "coordinator"  # Site (CRC): own site & trial
    PLATFORM_ADMIN = "platform_admin"  # Platform: models/monitoring; no PII
    AUDITOR = "auditor"  # Platform: read-only, all activity
    # Operational platform-tier roles (Gate RBAC-OPS) — NO sponsor scope; action-separated.
    MLOPS_ENGINEER = (
        "mlops_engineer"  # Platform: model lifecycle (drift/registry/promote)
    )
    LLMOPS_ENGINEER = "llmops_engineer"  # Platform: LLM ops (cost/guardrail), read-only


# --- role classification (drives scope resolution + RLS session vars) ---

CRO_ROLES: frozenset[Role] = frozenset({Role.STUDY_MANAGER, Role.CRA})
SPONSOR_ROLES: frozenset[Role] = frozenset({Role.SPONSOR_OVERSIGHT})
SITE_ROLES: frozenset[Role] = frozenset({Role.PRINCIPAL_INVESTIGATOR, Role.COORDINATOR})
# Platform tier: NO sponsor scope; reach platform/global tables via the is_platform GUC, never
# tenant rows (RLS fails closed). The two operational roles inherit this exactly (Gate RBAC-OPS).
PLATFORM_ROLES: frozenset[Role] = frozenset(
    {
        Role.PLATFORM_ADMIN,
        Role.AUDITOR,
        Role.MLOPS_ENGINEER,
        Role.LLMOPS_ENGINEER,
    }
)


# --- operational capabilities (Gate RBAC-OPS) ---
#
# An ACTION-based least-privilege axis layered ON TOP of the data-scope tier. It is NOT a
# tenancy mechanism — every capability-bearing role is platform-tier (no sponsor scope). It
# only draws the line between the two operational roles: the MLOps engineer owns the model
# lifecycle (reads + the write actions); the LLMOps engineer owns LLM operations (read only).
# platform_admin holds every capability (the superset); auditor holds the READS only (no action).


class Capability(StrEnum):
    """A platform operational capability (Gate RBAC-OPS) — the ENFORCED authorization point."""

    MLOPS_READ = (
        "mlops_read"  # drift (PSI/KS), model registry, champion/challenger traffic
    )
    MLOPS_WRITE = (
        "mlops_write"  # drift-run, model register, model promote (model lifecycle)
    )
    LLMOPS_READ = "llmops_read"  # LLM cost rollups, message_events / guardrail mix


_CAPABILITY_ROLES: dict[Capability, frozenset[Role]] = {
    Capability.MLOPS_READ: frozenset(
        {Role.PLATFORM_ADMIN, Role.AUDITOR, Role.MLOPS_ENGINEER}
    ),
    # The least-privilege point: the model-lifecycle WRITE actions exclude llmops_engineer AND
    # auditor (read-only). An LLMOps engineer is authorizationally barred from touching models.
    Capability.MLOPS_WRITE: frozenset({Role.PLATFORM_ADMIN, Role.MLOPS_ENGINEER}),
    Capability.LLMOPS_READ: frozenset(
        {Role.PLATFORM_ADMIN, Role.AUDITOR, Role.LLMOPS_ENGINEER}
    ),
}


def can_do(role: Role, capability: Capability) -> bool:
    """True if ``role`` is granted ``capability`` (Gate RBAC-OPS least-privilege table).

    platform_admin is the superset (all reads + all actions); auditor is read-only (all reads,
    no action); mlops_engineer is confined to the MLOps surface (read + write); llmops_engineer
    to LLMOps (read only). Sponsor/site/CRO roles hold no platform capability at all.
    """
    return role in _CAPABILITY_ROLES[capability]


# Roles permitted to see participant identities (site roles only, per api.md).
IDENTITY_ROLES: frozenset[Role] = SITE_ROLES

# Roles permitted to create/scope other users (admin authority flows down the hierarchy).
USER_ADMIN_ROLES: frozenset[Role] = frozenset(
    {
        Role.PLATFORM_ADMIN,
        Role.SPONSOR_OVERSIGHT,
        Role.STUDY_MANAGER,
        Role.PRINCIPAL_INVESTIGATOR,
    }
)


class ParticipantStatus(StrEnum):
    ACTIVE = "active"
    COMPLETED = "completed"
    WITHDRAWN = "withdrawn"
    CENSORED = "censored"


class InterventionKind(StrEnum):
    CALL = "call"
    VISIT_RESCHEDULE = "visit_reschedule"
    REMINDER = "reminder"
    NOTE = "note"
