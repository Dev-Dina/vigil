"""Routing service — scope-checked access + transitions for routing_state.

routing_state carries no RLS. The application-layer guard here is the sole mechanism
blocking sponsor-scoped sessions from reading routing state.

Two transition paths (specs/routing.md):
- **Drift-triggered fallback** (`handle_breach`): automatic, system-initiated. Rolls the
  champion DOWN to the last-known-good prior version, or suspends the regime if none.
  Exposed as a CALLABLE that consumes an opaque breach signal — routing does not compute,
  store, or define drift (that is observability). The signal DELIVERY mechanism is deferred.
- **Audited promotion** (`promote`): manual, MLOPS_WRITE (platform_admin + mlops_engineer; Gate
  RBAC-OPS). Rolls a version UP to champion. NEVER automatic, never by the worker or a drift
  signal. Honesty-hooked.

Every transition updates routing_state AND appends an audit_log row — nothing changes
silently. audit_log is read only to find a prior rollback target, never to source the
current champion.
"""

from __future__ import annotations

from dataclasses import dataclass

from vigil.core.scope import Scope
from vigil.db.models import ModelRegistry, RoutingState
from vigil.domain import Capability, can_do
from vigil.repositories import registry as registry_repo
from vigil.repositories import routing as routing_repo
from vigil.repositories.session import platform_session


class RoutingPermissionError(PermissionError):
    """Raised when a non-platform scope attempts to access routing_state."""


class PromotionError(Exception):
    """Raised when a promotion violates the safety/honesty rules (specs/routing.md)."""


class RegistrationError(Exception):
    """Raised when a model registration violates the governance/honesty rules (Gate M3)."""


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
# Audited promotion (manual, MLOPS_WRITE: platform_admin + mlops_engineer)
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
    """Promote ``model_version`` to champion (manual; MLOPS_WRITE: platform_admin + mlops_engineer).

    Safety rule (specs/routing.md § Decisions): promotion is ALWAYS manual and initiated by a
    model-lifecycle engineer (platform_admin or mlops_engineer); ``actor_user_id`` is that user's
    UUID and is NEVER NULL. Honesty hook:
    ``eval_provenance`` MUST be declared; synthetic-cohort eval is 'architecture_validation'
    (method, not clinical) — a record claiming clinical validation is rejected.
    ``model_card_ref`` MUST be non-null (enforced in the repository).
    """
    if not can_do(scope.role, Capability.MLOPS_WRITE):
        raise RoutingPermissionError(
            f"promotion denied: role {scope.role!r} lacks mlops_write; promotion is a manual "
            "model-lifecycle action for platform_admin + mlops_engineer only "
            "(specs/routing.md § Decisions; specs/domain.md § Operational roles)"
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
        # Gate M3: if this version is in the registry, this is a GOVERNED promotion of a registered,
        # offline-validated version — the validated card travels WITH it. An UNregistered version is
        # still promotable (backward-compatible with the B3 audited-promotion contract); the registry
        # only ENRICHES when present (it never blocks the existing path).
        registration = registry_repo.get_registration(
            session, regime=regime, model_version=model_version
        )
        card = (
            registration.model_card_ref
            if registration is not None and registration.model_card_ref
            else model_card_ref
        )
        champion = routing_repo.set_champion(
            session,
            regime=regime,
            model_version=model_version,
            model_card_ref=card,
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
            model_card_ref=card,
        )
        # Reflect the promotion in the catalog: this version → champion; the PRIOR champion is
        # RETAINED as 'retired' (reversible). Only done for registered versions (the catalog is the
        # governed surface); unregistered legacy promotions leave the catalog untouched.
        if registration is not None:
            registry_repo.mark_champion(
                session, regime=regime, model_version=model_version
            )
        return PromotionResult(
            regime=regime,
            from_version=from_version,
            to_version=model_version,
            actor_user_id=str(actor),
            audit_id=str(audit.id),
        )


# ---------------------------------------------------------------------------
# Model registry (Gate M3) — register validated weights → governed promotion
# ---------------------------------------------------------------------------


@dataclass(frozen=True, slots=True)
class RegistrationResult:
    regime: str
    model_version: str
    status: str  # 'challenger' | 'shadow'
    provenance: str
    actor_user_id: str
    registry_id: str
    audit_id: str


def register_model(
    scope: Scope,
    *,
    regime: str,
    model_version: str,
    artifact_ref: str,
    model_card_ref: str,
    metrics: dict,
    provenance: str,
    status: str = "challenger",
    notes: str = "",
) -> RegistrationResult:
    """Register a new, offline-validated model VERSION as a challenger/shadow (Gate M3).

    The ML/platform engineer brings a version IN: its identifier, its artifact reference (where the
    OFFLINE-trained weights live), the supplied OFFLINE validation ``metrics`` (RECORDED, never
    computed in-app), and a ``provenance`` label. It enters the catalog as ``challenger`` or
    ``shadow`` (NEVER auto-champion — promotion is the separate audited step) and is reflected in the
    ``routing_state`` projection so ``GET /monitoring/models`` shows it. The registration is audited.

    Governance + honesty (fail LOUD):
      - MLOPS_WRITE: platform_admin + mlops_engineer (``RoutingPermissionError`` otherwise); auditor /
        llmops_engineer / tenant roles cannot register.
      - ``status`` must be ``challenger`` or ``shadow`` (registration never makes a champion).
      - ``model_card_ref`` + ``provenance`` required; a ``clinical`` provenance claim is rejected
        (synthetic-cohort eval is method validity → ``architecture_validation``; a demo is
        ``demonstration``) — consistent with the promotion honesty hook.
      - ``metrics`` must be a non-empty dict — a registered model carries its REAL supplied
        validation metrics; we never fabricate or compute them here.
      - a version already registered for the regime is a hard error (no silent overwrite).
    """
    if not can_do(scope.role, Capability.MLOPS_WRITE):
        raise RoutingPermissionError(
            f"model registration denied: role {scope.role!r} lacks mlops_write; the registry is a "
            "model-lifecycle surface for platform_admin + mlops_engineer only "
            "(specs/routing.md § Model registry; specs/domain.md § Operational roles)"
        )
    if not scope.user_id:
        raise RegistrationError("registration requires a non-null actor_user_id")
    if status not in ("challenger", "shadow"):
        raise RegistrationError(
            f"register enters {status!r} but only 'challenger' or 'shadow' are allowed; "
            "registration NEVER makes a champion (promotion is the separate audited step)"
        )
    if not model_card_ref:
        raise RegistrationError("registration requires a non-null model_card_ref")
    if not artifact_ref:
        raise RegistrationError(
            "registration requires an artifact_ref (where the trained weights live)"
        )
    if not provenance:
        raise RegistrationError(
            "registration requires provenance (synthetic eval → 'architecture_validation'; "
            "a demo → 'demonstration')"
        )
    if "clinical" in provenance.lower():
        raise RegistrationError(
            "provenance claims clinical validation; only synthetic/architecture validation is "
            "available for T2D (specs/routing.md § Model registry honesty)"
        )
    if not isinstance(metrics, dict) or not metrics:
        raise RegistrationError(
            "registration requires the supplied OFFLINE validation metrics (a non-empty object); "
            "we RECORD the metrics shipped with the validated model, never compute them in-app"
        )

    import uuid as _uuid

    actor = _uuid.UUID(scope.user_id)
    with platform_session() as session:
        if (
            registry_repo.get_registration(
                session, regime=regime, model_version=model_version
            )
            is not None
        ):
            raise RegistrationError(
                f"model_version {model_version!r} is already registered for regime {regime!r}"
            )
        reg = registry_repo.insert_registration(
            session,
            regime=regime,
            model_version=model_version,
            status=status,
            artifact_ref=artifact_ref,
            model_card_ref=model_card_ref,
            metrics=metrics,
            provenance=provenance,
            registered_by=actor,
            notes=notes,
        )
        # Reflect the new version in the routing projection (challenger/shadow) so the monitoring
        # surface shows it. Champion-only surfacing is preserved: a challenger/shadow version is
        # never in the champion allowlist, so it cannot reach clinical reads.
        routing_repo.upsert_role_version(
            session,
            regime=regime,
            role=status,
            model_version=model_version,
            model_card_ref=model_card_ref,
            promoted_by=actor,
        )
        audit = routing_repo.write_routing_audit(
            session,
            action="model_register",
            actor_user_id=actor,
            target_id=str(reg.id),
            regime=regime,
            from_version=None,
            to_version=model_version,
            reason=f"register:{status}",
            eval_provenance=provenance,
            model_card_ref=model_card_ref,
        )
        return RegistrationResult(
            regime=regime,
            model_version=model_version,
            status=status,
            provenance=provenance,
            actor_user_id=str(actor),
            registry_id=str(reg.id),
            audit_id=str(audit.id),
        )


@dataclass(frozen=True, slots=True)
class RegistrationView:
    regime: str
    model_version: str
    status: str
    artifact_ref: str
    model_card_ref: str
    metrics: dict
    provenance: str
    notes: str
    registered_by: str | None
    registered_at: str


def list_registrations(
    scope: Scope, *, regime: str | None = None
) -> list[RegistrationView]:
    """List registered versions + their supplied metrics/provenance (PLATFORM/AUDITOR read).

    The informed-decision surface: an engineer sees each version's OFFLINE validation metrics and
    provenance BEFORE promoting. ``routing_state`` has no RLS, so the capability gate is the sole
    guard. The registry is an MLOps surface — MLOPS_READ (platform_admin, auditor, mlops_engineer);
    llmops_engineer is denied (it owns LLM ops, not the model lifecycle).
    """
    if not can_do(scope.role, Capability.MLOPS_READ):
        raise RoutingPermissionError(
            f"model registry read denied: role {scope.role!r} lacks mlops_read; the registry is an "
            "MLOps read surface (platform_admin, auditor, mlops_engineer) "
            "(specs/routing.md § Model registry; specs/domain.md § Operational roles)"
        )
    with platform_session() as session:
        rows = registry_repo.list_registrations(session, regime=regime)
        return [_to_registration_view(r) for r in rows]


def _to_registration_view(r: ModelRegistry) -> RegistrationView:
    return RegistrationView(
        regime=r.regime,
        model_version=r.model_version,
        status=r.status,
        artifact_ref=r.artifact_ref,
        model_card_ref=r.model_card_ref,
        metrics=dict(r.metrics),
        provenance=r.provenance,
        notes=r.notes,
        registered_by=str(r.registered_by) if r.registered_by is not None else None,
        registered_at=r.registered_at.isoformat(),
    )
