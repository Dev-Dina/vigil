"""Scoped administration (domain.md, api.md): user creation + assignment grants.

The subset rule is enforced HERE, before any write: a grantor may only create or grant a
scope that is a subset of their own, never outside their tenant. A sponsor-side admin's new
user is forced to the admin's own sponsor. Every action is written to the audit trail.

The router passes the caller's verified :class:`Scope`; the client never asserts target
tenancy. RLS is the backstop, but this layer fails LOUD first with a precise reason.
"""

from __future__ import annotations

import secrets
import uuid
from dataclasses import dataclass

from vigil.core.scope import Scope, ScopeError, ScopeTuple
from vigil.core.security import hash_password
from vigil.db.models import AssignmentGrant, User
from vigil.domain import (
    CRO_ROLES,
    PLATFORM_ROLES,
    SITE_ROLES,
    SPONSOR_ROLES,
    USER_ADMIN_ROLES,
    Role,
)
from vigil.repositories import users as user_repo
from vigil.repositories.session import scoped_session


class AdminError(Exception):
    """Authorization or validation failure in scoped administration. 403/400."""


@dataclass(frozen=True, slots=True)
class GrantSpec:
    sponsor_id: str
    trial_id: str | None = None
    site_id: str | None = None

    def as_tuple(self) -> ScopeTuple:
        return ScopeTuple(self.sponsor_id, self.trial_id, self.site_id)


def _ensure_admin(scope: Scope) -> None:
    if scope.role not in USER_ADMIN_ROLES:
        raise AdminError(f"role {scope.role} may not administer users")


def _forced_home_sponsor(scope: Scope, requested: str | None) -> str | None:
    """Sponsor-side admins force the new user's home sponsor to their own (no cross-tenant).

    Platform admins may set any (they create tenant roots). CRO/site admins bind to their
    own sponsor scope.
    """
    if scope.role in PLATFORM_ROLES:
        return requested
    own = scope.home_sponsor_id
    if own is None:
        # CRO admin: must name a sponsor they are scoped to.
        if requested is None:
            raise AdminError("home_sponsor_id required and must be within your scope")
        if requested not in scope.sponsor_ids:
            raise ScopeError(f"sponsor {requested} outside your scope")
        return requested
    if requested is not None and requested != own:
        raise ScopeError("cannot create a user outside your own sponsor")
    return own


def _check_subset(scope: Scope, grants: list[GrantSpec]) -> None:
    for g in grants:
        if not scope.covers(g.as_tuple()):
            raise ScopeError(f"grant {g.as_tuple()} is not a subset of your scope")


@dataclass(frozen=True, slots=True)
class CreatedUser:
    user_id: str
    email: str
    role: str
    home_sponsor_id: str | None
    home_cro_id: str | None


def create_user(
    scope: Scope,
    *,
    email: str,
    role: Role,
    home_sponsor_id: str | None,
    home_cro_id: str | None,
    initial_grants: list[GrantSpec],
    temp_password: str | None = None,
) -> CreatedUser:
    """Create a user bound to the caller's tenant, with subset-checked initial grants."""
    _ensure_admin(scope)

    home_sponsor = _forced_home_sponsor(scope, home_sponsor_id)
    cro_id = home_cro_id

    # Role/home consistency: exactly one of sponsor/cro for non-platform target users.
    if role in SPONSOR_ROLES | SITE_ROLES and home_sponsor is None:
        raise AdminError(f"{role} requires a home sponsor")
    if role in CRO_ROLES:
        cro_id = home_cro_id or scope.home_cro_id
        if cro_id is None:
            raise AdminError(f"{role} requires a home CRO")
        home_sponsor = None  # CRO users have no home sponsor (scope via grants)

    _check_subset(scope, initial_grants)

    password = temp_password or secrets.token_urlsafe(16)
    # Bind RLS to the target sponsor (or platform) so the write itself is tenant-checked.
    bind_sponsor = home_sponsor or (
        initial_grants[0].sponsor_id if initial_grants else None
    )

    if scope.is_platform:
        ctx = scoped_session(scope)
    else:
        ctx = scoped_session(scope, sponsor_id=bind_sponsor)

    with ctx as session:
        created: User = user_repo.create_user(
            session,
            email=email,
            password_hash=hash_password(password),
            role=str(role),
            home_sponsor_id=uuid.UUID(home_sponsor) if home_sponsor else None,
            home_cro_id=uuid.UUID(cro_id) if cro_id else None,
        )
        for g in initial_grants:
            user_repo.add_grant(
                session,
                user_id=created.id,
                sponsor_id=uuid.UUID(g.sponsor_id),
                trial_id=uuid.UUID(g.trial_id) if g.trial_id else None,
                site_id=uuid.UUID(g.site_id) if g.site_id else None,
                granted_by=uuid.UUID(scope.user_id),
            )
        user_repo.write_audit(
            session,
            actor_user_id=uuid.UUID(scope.user_id),
            action="user.create",
            target_type="user",
            target_id=str(created.id),
            sponsor_id=uuid.UUID(home_sponsor) if home_sponsor else None,
            detail={"role": str(role), "grants": len(initial_grants)},
        )
        return CreatedUser(
            user_id=str(created.id),
            email=created.email,
            role=created.role,
            home_sponsor_id=home_sponsor,
            home_cro_id=cro_id,
        )


@dataclass(frozen=True, slots=True)
class CreatedGrant:
    grant_id: str
    user_id: str
    sponsor_id: str
    trial_id: str | None
    site_id: str | None
    granted_by: str


def add_grant(scope: Scope, *, target_user_id: str, grant: GrantSpec) -> CreatedGrant:
    """Add an assignment grant (subset-checked), bump the target's scope_ver, audit it."""
    _ensure_admin(scope)
    if not scope.covers(grant.as_tuple()):
        raise ScopeError(f"grant {grant.as_tuple()} is not a subset of your scope")

    with scoped_session(scope, sponsor_id=grant.sponsor_id) as session:
        target = user_repo.get_by_id(session, uuid.UUID(target_user_id))
        if target is None:
            raise AdminError("target user not found in your scope")
        row: AssignmentGrant = user_repo.add_grant(
            session,
            user_id=target.id,
            sponsor_id=uuid.UUID(grant.sponsor_id),
            trial_id=uuid.UUID(grant.trial_id) if grant.trial_id else None,
            site_id=uuid.UUID(grant.site_id) if grant.site_id else None,
            granted_by=uuid.UUID(scope.user_id),
        )
        user_repo.bump_scope_ver(session, target)
        user_repo.write_audit(
            session,
            actor_user_id=uuid.UUID(scope.user_id),
            action="grant.add",
            target_type="user",
            target_id=target_user_id,
            sponsor_id=uuid.UUID(grant.sponsor_id),
            detail={"trial_id": grant.trial_id, "site_id": grant.site_id},
        )
        return CreatedGrant(
            grant_id=str(row.id),
            user_id=target_user_id,
            sponsor_id=grant.sponsor_id,
            trial_id=grant.trial_id,
            site_id=grant.site_id,
            granted_by=scope.user_id,
        )


def revoke_grant(scope: Scope, *, target_user_id: str, grant_id: str) -> None:
    _ensure_admin(scope)
    with scoped_session(
        scope, sponsor_id=next(iter(scope.sponsor_ids), None)
    ) as session:
        row = user_repo.get_grant(session, uuid.UUID(grant_id))
        if row is None:
            raise AdminError("grant not found in your scope")
        if not scope.covers(
            ScopeTuple(
                str(row.sponsor_id),
                str(row.trial_id) if row.trial_id else None,
                str(row.site_id) if row.site_id else None,
            )
        ):
            raise ScopeError("grant outside your scope")
        target = user_repo.get_by_id(session, row.user_id)
        user_repo.delete_grant(session, row)
        if target is not None:
            user_repo.bump_scope_ver(session, target)
        user_repo.write_audit(
            session,
            actor_user_id=uuid.UUID(scope.user_id),
            action="grant.revoke",
            target_type="user",
            target_id=target_user_id,
            sponsor_id=row.sponsor_id,
            detail={"grant_id": grant_id},
        )


def create_sponsor(scope: Scope, *, name: str) -> tuple[str, str]:
    """Platform-admin-only tenant-root creation. Returns (sponsor_id, name)."""
    if scope.role is not Role.PLATFORM_ADMIN:
        raise AdminError("only platform admin creates sponsors")
    from vigil.repositories import tenancy as tenancy_repo

    with scoped_session(scope) as session:
        sponsor = tenancy_repo.create_sponsor(session, name=name)
        user_repo.write_audit(
            session,
            actor_user_id=uuid.UUID(scope.user_id),
            action="sponsor.create",
            target_type="sponsor",
            target_id=str(sponsor.id),
            sponsor_id=sponsor.id,
            detail={"name": name},
        )
        return str(sponsor.id), name
