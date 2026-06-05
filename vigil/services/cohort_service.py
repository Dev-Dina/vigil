"""Cohort read path (api.md /cohort). RLS-filtered to the caller's sponsors/trials/sites.

The service opens a scope-bound session (which sets the RLS GUC) and asks the repository for
participants. It never injects a sponsor filter itself — Postgres returns only in-scope rows.
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
    updated_at: datetime


def list_cohort(
    scope: Scope, *, sponsor_id: str | None = None, limit: int = 50
) -> list[CohortRow]:
    with scoped_session(scope, sponsor_id=sponsor_id) as session:
        rows = tenancy_repo.list_participants(session, limit=limit)
        return [
            CohortRow(
                participant_id=str(p.id),
                trial_id=str(p.trial_id),
                site_id=str(p.site_id),
                risk_score=p.risk_score,
                risk_band=p.risk_band,
                updated_at=p.created_at,
            )
            for p in rows
        ]
