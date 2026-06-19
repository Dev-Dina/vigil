"""Model-registry repository (Gate M3) — the only DB path for ``model_registry``.

Platform-tier table (no RLS, no tenant key — same class as ``routing_state``); callers pass a
``platform_session``. Stores the CATALOG of registered model versions + the OFFLINE validation
metrics + provenance supplied with each. The current champion/challenger/shadow PROJECTION still
lives in ``routing_state``; this is the durable catalog the projection is drawn from.
"""

from __future__ import annotations

from sqlalchemy import asc, select
from sqlalchemy.orm import Session

from vigil.db.models import ModelRegistry


def insert_registration(
    session: Session,
    *,
    regime: str,
    model_version: str,
    status: str,
    artifact_ref: str,
    model_card_ref: str,
    metrics: dict,
    provenance: str,
    registered_by,  # uuid.UUID | None
    notes: str = "",
) -> ModelRegistry:
    """Register one model version into the catalog (challenger/shadow). Plain INSERT.

    Uniqueness ``(regime, model_version)`` is enforced by the DB; the service checks first so it can
    raise a clean domain error rather than surface an IntegrityError.
    """
    row = ModelRegistry(
        regime=regime,
        model_version=model_version,
        status=status,
        artifact_ref=artifact_ref,
        model_card_ref=model_card_ref,
        metrics=metrics,
        provenance=provenance,
        registered_by=registered_by,
        notes=notes,
    )
    session.add(row)
    session.flush()
    return row


def get_registration(
    session: Session, *, regime: str, model_version: str
) -> ModelRegistry | None:
    """Return the catalog row for ``(regime, model_version)``, or None if not registered."""
    return session.execute(
        select(ModelRegistry).where(
            ModelRegistry.regime == regime,
            ModelRegistry.model_version == model_version,
        )
    ).scalar_one_or_none()


def list_registrations(
    session: Session, *, regime: str | None = None
) -> list[ModelRegistry]:
    """All registered versions (optionally for one regime), stable order. Empty when none."""
    stmt = select(ModelRegistry)
    if regime is not None:
        stmt = stmt.where(ModelRegistry.regime == regime)
    stmt = stmt.order_by(asc(ModelRegistry.regime), asc(ModelRegistry.registered_at))
    return list(session.execute(stmt).scalars().all())


def mark_champion(
    session: Session, *, regime: str, model_version: str
) -> ModelRegistry:
    """Flip the catalog so ``model_version`` is the ``champion`` and the PRIOR champion is RETAINED.

    Sets the target row's status to ``champion`` and demotes any OTHER row in the regime currently
    at ``champion`` to ``retired`` (kept in the catalog with its metrics → reversible). Returns the
    promoted row; raises if the version is not registered (the service registers before promoting).
    """
    target = get_registration(session, regime=regime, model_version=model_version)
    if target is None:
        raise ValueError(
            f"cannot mark champion: {model_version!r} is not registered for regime {regime!r}"
        )
    prior_champions = session.execute(
        select(ModelRegistry).where(
            ModelRegistry.regime == regime,
            ModelRegistry.status == "champion",
            ModelRegistry.model_version != model_version,
        )
    ).scalars()
    for row in prior_champions:
        row.status = "retired"  # retained in the catalog (reversible), not deleted
    target.status = "champion"
    session.flush()
    return target
