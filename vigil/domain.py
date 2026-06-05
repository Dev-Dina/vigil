"""Domain enums and the canonical role set (domain.md).

Pure value objects — no I/O, no DB, no framework. Imported by every layer so the seven
roles and their tenancy semantics are defined in exactly one place.
"""

from __future__ import annotations

from enum import StrEnum


class Role(StrEnum):
    """The seven roles. snake_case enum values match the JWT ``role`` claim (api.md)."""

    STUDY_MANAGER = "study_manager"  # CRO: assigned sponsors & trials
    CRA = "cra"  # CRO: assigned sites within trials
    SPONSOR_OVERSIGHT = "sponsor_oversight"  # Sponsor: own sponsor, all trials
    PRINCIPAL_INVESTIGATOR = "principal_investigator"  # Site: own site & trial
    COORDINATOR = "coordinator"  # Site (CRC): own site & trial
    PLATFORM_ADMIN = "platform_admin"  # Platform: models/monitoring; no PII
    AUDITOR = "auditor"  # Platform: read-only, all activity


# --- role classification (drives scope resolution + RLS session vars) ---

CRO_ROLES: frozenset[Role] = frozenset({Role.STUDY_MANAGER, Role.CRA})
SPONSOR_ROLES: frozenset[Role] = frozenset({Role.SPONSOR_OVERSIGHT})
SITE_ROLES: frozenset[Role] = frozenset({Role.PRINCIPAL_INVESTIGATOR, Role.COORDINATOR})
PLATFORM_ROLES: frozenset[Role] = frozenset({Role.PLATFORM_ADMIN, Role.AUDITOR})

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
