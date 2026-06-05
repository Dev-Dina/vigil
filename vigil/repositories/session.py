"""The scoped session opener — the single chokepoint that binds a DB session to a Scope.

It sets the RLS GUCs (``app.current_sponsor``, ``app.is_platform``) from the verified
:class:`~vigil.core.scope.Scope` BEFORE any query runs, so Postgres — not application code —
decides what rows are visible. Every repository method receives a session opened here; there
is no other sanctioned way to obtain one for request handling.

``SET LOCAL`` scopes the GUC to the surrounding transaction, so the value cannot leak to the
next checkout of a pooled connection.
"""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager

from sqlalchemy import text
from sqlalchemy.orm import Session

from vigil.core.scope import Scope
from vigil.db.engine import get_session_factory

# set_config(name, value, is_local=true) — transaction-local GUC; unlike SET LOCAL it accepts
# bind parameters, so the sponsor uuid is never string-interpolated into SQL.
_SET_GUC = text("SELECT set_config(:name, :value, true)")


def _set_guc(session: Session, name: str, value: str) -> None:
    session.execute(_SET_GUC, {"name": name, "value": value})


def _bind_rls(session: Session, *, sponsor: str | None, is_platform: bool) -> None:
    _set_guc(session, "app.is_platform", "on" if is_platform else "off")
    _set_guc(session, "app.current_sponsor", sponsor or "")


@contextmanager
def scoped_session(scope: Scope, *, sponsor_id: str | None = None) -> Iterator[Session]:
    """Open a transaction with RLS GUCs set from ``scope``.

    ``sponsor_id`` selects which in-scope sponsor binds RLS for multi-sponsor (CRO) callers;
    it is validated against the scope. Platform users bind no sponsor and instead set
    ``app.is_platform = on`` (cross-tenant read of the bespoke tables only).
    """
    factory = get_session_factory()
    session = factory()
    try:
        with session.begin():
            if scope.is_platform:
                _bind_rls(session, sponsor=None, is_platform=True)
            else:
                bound = scope.rls_sponsor_id(sponsor_id)
                _bind_rls(session, sponsor=bound, is_platform=False)
            yield session
    finally:
        session.close()


@contextmanager
def auth_lookup_session() -> Iterator[Session]:
    """Narrow pre-auth session for login/refresh ONLY.

    Before a Scope exists we must read the authenticating user's row (by exact email) and
    their grants to resolve scope. This sets ``app.is_platform = on`` so those bespoke
    cross-tenant tables (``user``, ``assignment_grant``) admit the read. It is used solely by
    the auth service's resolver — never exposed to request handlers, and it only ever reads,
    never writes tenant data.
    """
    factory = get_session_factory()
    session = factory()
    try:
        with session.begin():
            _bind_rls(session, sponsor=None, is_platform=True)
            yield session
    finally:
        session.close()


@contextmanager
def platform_session() -> Iterator[Session]:
    """A platform-privileged session for bootstrap/seed only (no request Scope yet).

    Sets ``app.is_platform = on`` so the tenant-root and bespoke tables admit writes during
    sponsor/first-admin creation. NOT reachable from request handlers.
    """
    factory = get_session_factory()
    session = factory()
    try:
        with session.begin():
            _bind_rls(session, sponsor=None, is_platform=True)
            yield session
    finally:
        session.close()


@contextmanager
def sponsor_bootstrap_session(sponsor_id: str) -> Iterator[Session]:
    """Bind RLS to a specific sponsor for SEED/bootstrap inserts of that sponsor's tenant
    rows (trial/site/participant). The default tenant policy has no platform bypass by design
    — platform users must never touch participant data — so seeding a sponsor's rows requires
    binding to that sponsor. NOT reachable from request handlers."""
    factory = get_session_factory()
    session = factory()
    try:
        with session.begin():
            _bind_rls(session, sponsor=sponsor_id, is_platform=False)
            yield session
    finally:
        session.close()
