"""Drift-metric repository (Gate M1) — the only DB path for ``drift_metric``.

Platform-tier table (no RLS, no tenant key — like routing_state); callers pass a
``platform_session``. Stores ONLY computed scalar PSI/KS points; never any tenant data.
"""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import desc, select
from sqlalchemy.orm import Session

from vigil.db.models import DriftMetric


def insert_drift_point(
    session: Session,
    *,
    regime: str,
    model_version: str,
    distribution: str,
    metric: str,
    value: float,
    threshold: float,
    breached: bool,
    reference_n: int,
    current_n: int,
    synthetic: bool,
    constructed_demo: bool,
    note: str = "",
) -> DriftMetric:
    """Append one computed drift point. Plain INSERT (history; each run appends new points)."""
    row = DriftMetric(
        regime=regime,
        model_version=model_version,
        distribution=distribution,
        metric=metric,
        value=value,
        threshold=threshold,
        breached=breached,
        reference_n=reference_n,
        current_n=current_n,
        synthetic=synthetic,
        constructed_demo=constructed_demo,
        note=note,
    )
    session.add(row)
    session.flush()
    return row


def list_recent_drift_points(session: Session, *, limit: int = 50) -> list[DriftMetric]:
    """Most-recent computed drift points (newest first). Empty list when none computed yet."""
    return list(
        session.execute(
            select(DriftMetric).order_by(desc(DriftMetric.computed_at)).limit(limit)
        )
        .scalars()
        .all()
    )


def latest_computed_at(session: Session) -> datetime | None:
    """Timestamp of the newest drift point, or None if the table is empty."""
    return session.execute(
        select(DriftMetric.computed_at).order_by(desc(DriftMetric.computed_at)).limit(1)
    ).scalar_one_or_none()
