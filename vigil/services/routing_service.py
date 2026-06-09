"""Routing service — scope-checked access to routing_state (specs/routing.md § Tenancy).

routing_state carries no RLS. The application-layer guard here is the sole mechanism
blocking sponsor-scoped sessions from reading routing state.
"""

from __future__ import annotations

from vigil.core.scope import Scope
from vigil.db.models import RoutingState
from vigil.repositories import routing as routing_repo
from vigil.repositories.session import platform_session


class RoutingPermissionError(PermissionError):
    """Raised when a non-platform scope attempts to access routing_state."""


def get_champion(scope: Scope, *, regime: str) -> RoutingState:
    """Return the champion RoutingState for a regime.

    Raises RoutingPermissionError for non-platform scopes — routing_state has no RLS;
    this check is the app-layer guard (specs/routing.md § Tenancy, invariant iii).
    Raises ValueError if no champion row exists for the regime.
    """
    if not scope.is_platform:
        raise RoutingPermissionError(
            f"routing_state access denied: role {scope.role!r} is not platform-scoped; "
            "only platform_admin and auditor may read routing state "
            "(specs/routing.md § Tenancy)"
        )
    with platform_session() as session:
        row = routing_repo.get_champion(session, regime=regime)
    if row is None:
        raise ValueError(f"No champion model registered for regime {regime!r}")
    return row
