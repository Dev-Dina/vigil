"""The sacred cross-tenant leakage test (CLAUDE.md definition of done).

Create data as Sponsor A; authenticate as Sponsor B; assert B cannot see A — and that the
sponsor wall holds for the CRO user staffed on A only, and for the subset rule on user
creation. Isolation is enforced by Postgres RLS, not application WHERE clauses, so these
assertions exercise the engine through the scope-bound session and the admin service.
"""

from __future__ import annotations

import uuid

import pytest

from vigil.core.scope import Scope, ScopeError
from vigil.domain import Role
from vigil.repositories import tenancy as tenancy_repo
from vigil.repositories import users as user_repo
from vigil.repositories.session import auth_lookup_session, scoped_session
from vigil.services import admin_service
from vigil.services.admin_service import AdminError, GrantSpec
from vigil.services.scope_resolver import resolve_scope


def _scope_for(email: str) -> Scope:
    """Resolve a seeded user's scope exactly as login would (from DB rows, not client)."""
    with auth_lookup_session() as session:
        user = user_repo.get_by_email(session, email)
        assert user is not None, f"seed user {email} missing"
        return resolve_scope(session, user)


# --------------------------------------------------------------------------- RLS reads
def test_coordinator_b_cannot_see_sponsor_a_participant(migrated_db):
    ids = migrated_db
    scope_b = _scope_for("coord.b@vigil.example")

    with scoped_session(scope_b) as session:
        # B's own participant is visible.
        own = tenancy_repo.get_participant(session, uuid.UUID(ids["participant_b"]))
        assert own is not None
        # Sponsor A's participant is INVISIBLE to B (RLS, not a missing WHERE).
        leaked = tenancy_repo.get_participant(session, uuid.UUID(ids["participant_a"]))
        assert leaked is None
        # And a list only ever returns B's rows.
        rows = tenancy_repo.list_participants(session, limit=100)
        assert {str(r.sponsor_id) for r in rows} == {ids["sponsor_b"]}


def test_coordinator_a_sees_only_sponsor_a(migrated_db):
    ids = migrated_db
    scope_a = _scope_for("coord.a@vigil.example")
    with scoped_session(scope_a) as session:
        rows = tenancy_repo.list_participants(session, limit=100)
        assert {str(r.sponsor_id) for r in rows} == {ids["sponsor_a"]}


def test_cro_manager_staffed_on_a_cannot_see_b(migrated_db):
    ids = migrated_db
    scope = _scope_for("cro.manager@vigil.example")
    # The CRO manager's scope is exactly Sponsor A (single grant); B is out of scope.
    assert scope.sponsor_ids == {ids["sponsor_a"]}

    # Binding RLS to A: sees A, not B.
    with scoped_session(scope, sponsor_id=ids["sponsor_a"]) as session:
        rows = tenancy_repo.list_participants(session, limit=100)
        assert {str(r.sponsor_id) for r in rows} == {ids["sponsor_a"]}

    # Attempting to bind to Sponsor B is refused before any query runs.
    with pytest.raises(ScopeError):
        with scoped_session(scope, sponsor_id=ids["sponsor_b"]) as session:
            tenancy_repo.list_participants(session)


def test_sponsor_b_sponsor_row_isolated(migrated_db):
    ids = migrated_db
    scope_b = _scope_for("oversight.b@vigil.example")
    with scoped_session(scope_b) as session:
        assert (
            tenancy_repo.get_sponsor(session, uuid.UUID(ids["sponsor_b"])) is not None
        )
        # The sponsor tenant-root row for A is invisible to B.
        assert tenancy_repo.get_sponsor(session, uuid.UUID(ids["sponsor_a"])) is None


# ------------------------------------------------------- scoped administration / subset
def test_sponsor_a_admin_cannot_create_user_in_sponsor_b(migrated_db):
    ids = migrated_db
    admin_scope = _scope_for("oversight.a@vigil.example")
    # A's admin trying to plant a user in Sponsor B must be refused (subset rule).
    with pytest.raises((ScopeError, AdminError)):
        admin_service.create_user(
            admin_scope,
            email="intruder@vigil.example",
            role=Role.SPONSOR_OVERSIGHT,
            home_sponsor_id=ids["sponsor_b"],
            home_cro_id=None,
            initial_grants=[],
        )


def test_sponsor_a_admin_cannot_grant_into_sponsor_b(migrated_db):
    ids = migrated_db
    admin_scope = _scope_for("oversight.a@vigil.example")
    with pytest.raises(ScopeError):
        admin_service.add_grant(
            admin_scope,
            target_user_id=ids["cro_manager"],
            grant=GrantSpec(sponsor_id=ids["sponsor_b"]),
        )


def test_sponsor_a_admin_can_create_within_own_sponsor_and_audits(migrated_db):
    ids = migrated_db
    admin_scope = _scope_for("oversight.a@vigil.example")
    created = admin_service.create_user(
        admin_scope,
        email="newcrc@vigil.example",
        role=Role.COORDINATOR,
        home_sponsor_id=ids["sponsor_a"],
        home_cro_id=None,
        initial_grants=[],
    )
    assert created.home_sponsor_id == ids["sponsor_a"]

    # The action is on the audit trail, visible within Sponsor A's scope.
    with scoped_session(admin_scope) as session:
        actions = {row.action for row in user_repo.list_audit(session)}
    assert "user.create" in actions
