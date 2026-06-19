"""Drift-metric repository (Gate M1) — the only DB path for ``drift_metric``.

Platform-tier table (no RLS, no tenant key — like routing_state); callers pass a
``platform_session``. Stores ONLY computed scalar PSI/KS points; never any tenant data.
"""

from __future__ import annotations

import uuid
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


def latest_breached_state(
    session: Session, *, regime: str, model_version: str, metric: str
) -> bool | None:
    """Breached flag of the most-recent PRIOR point for this ``(regime, model_version, metric)``.

    Gate M2 EDGE detection: ``None`` = no prior point for this series; ``False`` = prior point was
    NOT breached; ``True`` = prior point was breached. The producer alerts only on a transition INTO
    breach (``not latest_breached_state`` → no prior or prior-not-breached), so a re-run while the
    same metric is still breached does NOT re-alert (the analog of the crossing non-high→high edge).
    Filtering by ``metric`` keeps the series unambiguous (one psi + one ks per run).
    """
    return session.execute(
        select(DriftMetric.breached)
        .where(
            DriftMetric.regime == regime,
            DriftMetric.model_version == model_version,
            DriftMetric.metric == metric,
        )
        .order_by(desc(DriftMetric.computed_at))
        .limit(1)
    ).scalar_one_or_none()


def get_drift_point(session: Session, point_id: uuid.UUID) -> DriftMetric | None:
    """Load a single drift point by id (platform-tier table; no RLS). None if absent."""
    return session.get(DriftMetric, point_id)


def mark_drift_point_notified(session: Session, point_id: uuid.UUID) -> bool:
    """Flip ``notified`` true for one drift point (Gate M2 send-once). False if the row is absent.

    Called ONLY after a successful alert send, so an anchor point is notified at most once; a
    transient send failure leaves it false so the Arq job retries (bounded by ``max_tries``).
    """
    point = session.get(DriftMetric, point_id)
    if point is None:
        return False
    point.notified = True
    session.flush()
    return True
