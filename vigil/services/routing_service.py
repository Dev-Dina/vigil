"""Routing service — scope-checked access + transitions for routing_state.

routing_state carries no RLS. The application-layer guard here is the sole mechanism
blocking sponsor-scoped sessions from reading routing state.

Two transition paths (specs/routing.md):
- **Drift-triggered fallback** (`handle_breach`): automatic, system-initiated. Rolls the
  champion DOWN to the last-known-good prior version, or suspends the regime if none.
  Exposed as a CALLABLE that consumes an opaque breach signal — routing does not compute,
  store, or define drift (that is observability). The signal DELIVERY mechanism is deferred.
- **Audited promotion** (`promote`): manual, platform_admin only. Rolls a version UP to
  champion. NEVER automatic, never by the worker or a drift signal. Honesty-hooked.

Every transition updates routing_state AND appends an audit_log row — nothing changes
silently. audit_log is read only to find a prior rollback target, never to source the
current champion.
"""

from __future__ import annotations

from dataclasses import dataclass

from vigil.core.scope import Scope
from vigil.db.models import RoutingState
from vigil.domain import Role
from vigil.repositories import routing as routing_repo
from vigil.repositories.session import platform_session


class RoutingPermissionError(PermissionError):
    """Raised when a non-platform scope attempts to access routing_state."""


class PromotionError(Exception):
    """Raised when a promotion violates the safety/honesty rules (specs/routing.md)."""


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


# ---------------------------------------------------------------------------
# Drift-triggered fallback (automatic, system-initiated)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class BreachSignal:
    """The minimal drift-breach input routing CONSUMES (subset of api.md DriftPoint).

    Routing does not produce, store, or define how ``breached`` is measured — that is
    observability scope. The delivery mechanism (poll/event/call) is deferred; this gate
    implements the REACTION as a callable taking this opaque signal.
    """

    regime: str
    model_version: str
    breached: bool


@dataclass(frozen=True, slots=True)
class FallbackResult:
    regime: str
    outcome: str  # "rolled_back" | "suspended" | "noop"
    from_version: str | None
    to_version: str | None
    health: str
    audit_id: str | None


def handle_breach(breach: BreachSignal) -> FallbackResult:
    """React to a drift-breach signal for a regime's champion (automatic fallback).

    On ``breached=True`` for the CURRENT champion: roll the champion down to the
    last-known-good prior version (history query) and audit it; if there is no prior
    champion, set ``health='fallback'`` (scoring suspended pending manual review) and audit
    that. Fallback NEVER auto-promotes the shadow — it targets a prior champion only.
    System-initiated: ``actor_user_id=NULL``. No-op if not breached or version is stale.
    """
    if not breach.breached:
        return FallbackResult(
            breach.regime, "noop", None, None, "healthy", audit_id=None
        )

    with platform_session() as session:
        champion = routing_repo.get_champion(session, regime=breach.regime)
        if champion is None or champion.model_version != breach.model_version:
            # The breached version is not the current champion — nothing to roll back.
            return FallbackResult(
                breach.regime,
                "noop",
                None,
                None,
                champion.health if champion else "healthy",
                audit_id=None,
            )

        breached_version = champion.model_version
        target_version, target_card = routing_repo.last_known_good_champion(
            session, regime=breach.regime, breached_version=breached_version
        )

        if target_version is None:
            # No prior champion → suspend the regime (per spec step 3). Do NOT promote the
            # shadow — that is a separate MANUAL promotion.
            routing_repo.set_health(
                session, regime=breach.regime, role="champion", health="fallback"
            )
            audit = routing_repo.write_routing_audit(
                session,
                action="model_fallback",
                actor_user_id=None,
                target_id=str(champion.id),
                regime=breach.regime,
                from_version=breached_version,
                to_version=None,
                reason="drift_breach",
                eval_provenance="architecture_validation",
                model_card_ref=champion.model_card_ref,
            )
            return FallbackResult(
                breach.regime,
                "suspended",
                breached_version,
                None,
                "fallback",
                audit_id=str(audit.id),
            )

        card = target_card or champion.model_card_ref
        routing_repo.set_champion(
            session,
            regime=breach.regime,
            model_version=target_version,
            model_card_ref=card,
            promoted_by=None,  # system-initiated
        )
        audit = routing_repo.write_routing_audit(
            session,
            action="model_fallback",
            actor_user_id=None,
            target_id=str(champion.id),
            regime=breach.regime,
            from_version=breached_version,
            to_version=target_version,
            reason="drift_breach",
            eval_provenance="architecture_validation",
            model_card_ref=card,
        )
        return FallbackResult(
            breach.regime,
            "rolled_back",
            breached_version,
            target_version,
            "healthy",
            audit_id=str(audit.id),
        )


# ---------------------------------------------------------------------------
# Audited promotion (manual, platform_admin only)
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class PromotionResult:
    regime: str
    from_version: str | None
    to_version: str
    actor_user_id: str
    audit_id: str


def promote(
    scope: Scope,
    *,
    regime: str,
    model_version: str,
    model_card_ref: str,
    eval_provenance: str,
    reason: str = "manual_promotion",
) -> PromotionResult:
    """Promote ``model_version`` to champion for a regime (manual, platform_admin only).

    Safety rule (specs/routing.md § Decisions): promotion is ALWAYS manual and initiated by
    a platform_admin; ``actor_user_id`` is the admin's UUID and is NEVER NULL. Honesty hook:
    ``eval_provenance`` MUST be declared; synthetic-cohort eval is 'architecture_validation'
    (method, not clinical) — a record claiming clinical validation is rejected.
    ``model_card_ref`` MUST be non-null (enforced in the repository).
    """
    if scope.role is not Role.PLATFORM_ADMIN:
        raise RoutingPermissionError(
            f"promotion denied: role {scope.role!r} is not platform_admin; promotion is "
            "always manual and platform_admin-only (specs/routing.md § Decisions)"
        )
    if not scope.user_id:
        raise PromotionError("promotion requires a non-null actor_user_id")
    if not model_card_ref:
        raise PromotionError("promotion requires a non-null model_card_ref")
    if not eval_provenance:
        raise PromotionError(
            "promotion requires eval_provenance (synthetic eval → 'architecture_validation')"
        )
    if "clinical" in eval_provenance.lower():
        raise PromotionError(
            "eval_provenance claims clinical validation; only synthetic/architecture "
            "validation is available for T2D (specs/routing.md § Audited promotion honesty)"
        )

    import uuid as _uuid

    actor = _uuid.UUID(scope.user_id)
    with platform_session() as session:
        current = routing_repo.get_champion(session, regime=regime)
        from_version = current.model_version if current is not None else None
        champion = routing_repo.set_champion(
            session,
            regime=regime,
            model_version=model_version,
            model_card_ref=model_card_ref,
            promoted_by=actor,
        )
        audit = routing_repo.write_routing_audit(
            session,
            action="model_promote",
            actor_user_id=actor,
            target_id=str(champion.id),
            regime=regime,
            from_version=from_version,
            to_version=model_version,
            reason=reason,
            eval_provenance=eval_provenance,
            model_card_ref=model_card_ref,
        )
        return PromotionResult(
            regime=regime,
            from_version=from_version,
            to_version=model_version,
            actor_user_id=str(actor),
            audit_id=str(audit.id),
        )
