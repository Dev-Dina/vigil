"""Routing-state repository (specs/routing.md § Regime routing).

Platform-only access: routing_state is a global/infrastructure table (no RLS).
Only platform-session callers should invoke these functions.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session

from vigil.db.models import RoutingState


def get_champion(session: Session, *, regime: str) -> RoutingState | None:
    """Return the champion RoutingState row for regime, or None if absent."""
    return session.execute(
        select(RoutingState).where(
            RoutingState.regime == regime,
            RoutingState.role == "champion",
        )
    ).scalar_one_or_none()
