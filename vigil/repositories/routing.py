"""Routing-state repository (specs/routing.md § Regime routing).

Platform-only access: routing_state is a global/infrastructure table (no RLS).
Only platform-session callers should invoke these functions.

Routing transitions (fallback/promotion) update routing_state (the current projection)
AND append an immutable ``audit_log`` row. routing_state is NEVER read to source the
current champion from history; ``audit_log`` is read ONLY to find a prior version to roll
back to (the permitted history query per specs/routing.md § Decisions).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from vigil.db.models import AuditLog, RoutingState

#: audit_log actions for routing transitions (specs/routing.md § Audited promotion).
ROUTING_ACTIONS = ("model_promote", "model_demote", "model_fallback")


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


def last_known_good_champion(
    session: Session, *, regime: str, breached_version: str
) -> tuple[str | None, str | None]:
    """The most recent prior champion (version, model_card_ref) for a regime — a HISTORY
    query (permitted per specs/routing.md § Decisions: finding a rollback target, NOT
    sourcing the current champion).

    Walks ``audit_log`` routing transitions newest-first and returns the first one whose
    ``to_version`` differs from the breached version — i.e. the champion the regime ran
    BEFORE the breached one, together with the card that transition recorded. Returns
    ``(None, None)`` when no prior champion exists (caller then suspends the regime).
    """
    rows = session.execute(
        select(AuditLog)
        .where(
            AuditLog.action.in_(ROUTING_ACTIONS),
            AuditLog.detail["regime"].astext == regime,
        )
        .order_by(desc(AuditLog.created_at))
    ).scalars()
    for row in rows:
        to_version = row.detail.get("to_version")
        if to_version and to_version != breached_version:
            return to_version, row.detail.get("model_card_ref")
    return None, None


def set_champion(
    session: Session,
    *,
    regime: str,
    model_version: str,
    model_card_ref: str,
    promoted_by: uuid.UUID | None,
) -> RoutingState:
    """Update the champion routing_state row for a regime (current projection).

    Sets the new version/card, records ``promoted_by`` (NULL for system fallback), stamps
    ``promoted_at``, and resets ``health`` to 'healthy'. Raises if no champion row exists.
    """
    row = get_champion(session, regime=regime)
    if row is None:
        raise ValueError(f"No champion routing_state row for regime {regime!r}")
    row.model_version = model_version
    row.model_card_ref = model_card_ref
    row.promoted_by = promoted_by
    row.promoted_at = datetime.now(tz=timezone.utc)
    row.health = "healthy"
    session.flush()
    return row


def set_health(
    session: Session, *, regime: str, role: str, health: str
) -> RoutingState:
    """Set the ``health`` of a routing_state row (e.g. 'fallback' when scoring is suspended)."""
    row = session.execute(
        select(RoutingState).where(
            RoutingState.regime == regime, RoutingState.role == role
        )
    ).scalar_one_or_none()
    if row is None:
        raise ValueError(f"No routing_state row for regime {regime!r} role {role!r}")
    row.health = health
    session.flush()
    return row


def write_routing_audit(
    session: Session,
    *,
    action: str,
    actor_user_id: uuid.UUID | None,
    target_id: str | None,
    regime: str,
    from_version: str | None,
    to_version: str | None,
    reason: str,
    eval_provenance: str,
    model_card_ref: str,
) -> AuditLog:
    """Append an immutable routing-transition row to ``audit_log`` (sponsor_id=NULL,
    platform-only visible via the audit_scope policy). Mirrors ``write_score_audit``.

    ``model_card_ref`` MUST be non-null/non-empty — a transition without a model card is a
    hard error (specs/routing.md § Audited promotion honesty hook).
    """
    if not model_card_ref:
        raise ValueError(
            f"routing audit {action!r} for regime {regime!r} has empty model_card_ref "
            "(specs/routing.md § Audited promotion: model_card_ref MUST be non-null)"
        )
    row = AuditLog(
        actor_user_id=actor_user_id,
        action=action,
        target_type="routing_state",
        target_id=target_id,
        sponsor_id=None,  # platform-scoped; visible only to platform sessions (audit_scope)
        detail={
            "from_version": from_version,
            "to_version": to_version,
            "regime": regime,
            "reason": reason,
            "eval_provenance": eval_provenance,
            "model_card_ref": model_card_ref,
        },
    )
    session.add(row)
    session.flush()
    return row
