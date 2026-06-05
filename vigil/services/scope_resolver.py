"""Resolve a user's DB rows into the immutable :class:`Scope` (domain.md tenancy rules).

This is the single place that turns identity → scope. Sponsor/site users get a fixed tuple;
CRO users get the union of their ``assignment_grant`` rows; platform users get no sponsor
scope. The result is written into the JWT and never re-derived from client input.
"""

from __future__ import annotations

from sqlalchemy.orm import Session

from vigil.core.scope import Scope, ScopeError, ScopeTuple
from vigil.db.models import User
from vigil.domain import CRO_ROLES, PLATFORM_ROLES, SITE_ROLES, SPONSOR_ROLES, Role
from vigil.repositories import users as user_repo


def resolve_scope(session: Session, user: User) -> Scope:
    role = Role(user.role)
    tuples = _resolve_tuples(session, user, role)
    return Scope(
        user_id=str(user.id),
        role=role,
        home_sponsor_id=str(user.home_sponsor_id) if user.home_sponsor_id else None,
        home_cro_id=str(user.home_cro_id) if user.home_cro_id else None,
        tuples=tuples,
        scope_ver=user.scope_ver,
    )


def _resolve_tuples(session: Session, user: User, role: Role) -> tuple[ScopeTuple, ...]:
    if role in PLATFORM_ROLES:
        return ()

    if role in SPONSOR_ROLES:
        if user.home_sponsor_id is None:
            raise ScopeError("sponsor user without home_sponsor_id")
        # Fixed to (own sponsor, *, *): every trial/site under the home sponsor.
        return (ScopeTuple(sponsor_id=str(user.home_sponsor_id)),)

    if role in SITE_ROLES:
        if not (user.home_sponsor_id and user.home_trial_id and user.home_site_id):
            raise ScopeError("site user missing fixed sponsor/trial/site")
        return (
            ScopeTuple(
                sponsor_id=str(user.home_sponsor_id),
                trial_id=str(user.home_trial_id),
                site_id=str(user.home_site_id),
            ),
        )

    if role in CRO_ROLES:
        grants = user_repo.list_grants(session, user.id)
        if not grants:
            # A CRO user with no grants reaches nothing — fail-closed, not blanket.
            return ()
        return tuple(
            ScopeTuple(
                sponsor_id=str(g.sponsor_id),
                trial_id=str(g.trial_id) if g.trial_id else None,
                site_id=str(g.site_id) if g.site_id else None,
            )
            for g in grants
        )

    raise ScopeError(f"unhandled role {role}")
