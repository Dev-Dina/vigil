"""Cohort read path (api.md /cohort). RLS-filtered to caller's sponsors/trials/sites.

Opens a scope-bound session (sets RLS GUC); never injects a sponsor filter itself —
Postgres returns only in-scope rows.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from vigil.core.scope import Scope
from vigil.repositories import tenancy as tenancy_repo
from vigil.repositories.session import scoped_session


@dataclass(frozen=True, slots=True)
class CohortRow:
    participant_id: str
    trial_id: str
    site_id: str
    risk_score: float
    risk_band: str
    top_factors: list[str]
    updated_at: datetime
    synthetic: bool


def list_cohort(
    scope: Scope, *, sponsor_id: str | None = None, limit: int = 50
) -> list[CohortRow]:
    # TODO(phase5): join participant_score for top_factors + real synthetic flag.
    # For now derive from participant; synthetic=True (all seed data is synthetic).
    with scoped_session(scope, sponsor_id=sponsor_id) as session:
        rows = tenancy_repo.list_participants(session, limit=limit)
        return [
            CohortRow(
                participant_id=str(p.id),
                trial_id=str(p.trial_id),
                site_id=str(p.site_id),
                risk_score=p.risk_score,
                risk_band=p.risk_band,
                top_factors=[],  # TODO(phase5): from participant_score
                updated_at=p.created_at,
                synthetic=True,  # TODO(phase5): read from participant_score.synthetic
            )
            for p in rows
        ]
