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


def get_shadow(session: Session, *, regime: str) -> RoutingState | None:
    """Return the shadow RoutingState row for regime, or None if absent."""
    return session.execute(
        select(RoutingState).where(
            RoutingState.regime == regime,
            RoutingState.role == "shadow",
        )
    ).scalar_one_or_none()


def champion_model_versions(session: Session) -> set[str]:
    """All champion ``model_version`` strings across regimes — the surfacing allowlist.

    Used by clinical-read paths (``GET /participants/{id}/risk``) to filter
    ``participant_score`` to champion rows by construction. The set ADMITS only
    champion versions; challenger/shadow versions are excluded because they never
    carry ``role == 'champion'``. Regime-agnostic on purpose: the participant→regime
    mapping is not yet threaded, and an allowlist of champion versions is correct
    without it (it can never surface a non-champion row).
    """
    return set(
        session.execute(
            select(RoutingState.model_version).where(RoutingState.role == "champion")
        ).scalars()
    )
