"""Cohort read path (api.md /cohort). RLS-filtered to caller's sponsors/trials/sites.

Opens a scope-bound session (sets RLS GUC); never injects a sponsor filter itself —
Postgres returns only in-scope rows.

Champion-only surfacing (specs/routing.md § (i)) is preserved exactly as in B2c:
- ``risk_score`` / ``risk_band`` come from the ``participant`` DENORM cache, which ONLY the
  champion scoring job writes — shadow/challenger never touch it.
- ``synthetic`` / ``top_factors`` come from the champion ``participant_score`` row, read through
  the champion allowlist (``champion_model_versions`` → ``champion_scores_by_participant``), so
  a shadow/challenger row can never be surfaced. A participant with no champion score row gets
  the safe-synthetic default (True — never falsely claims real) and empty factors.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from vigil.core.scope import Scope
from vigil.repositories import routing as routing_repo
from vigil.repositories import scoring as scoring_repo
from vigil.repositories import tenancy as tenancy_repo
from vigil.repositories.session import platform_session, scoped_session


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
    # Champion allowlist from the platform routing table (not sponsor-scoped).
    with platform_session() as session:
        champion_versions = routing_repo.champion_model_versions(session)

    with scoped_session(scope, sponsor_id=sponsor_id) as session:
        rows = tenancy_repo.list_participants(session, limit=limit)
        # Batch champion-only read: synthetic + top_factors come from the CHAMPION row only.
        champ_scores = scoring_repo.champion_scores_by_participant(
            session,
            [p.id for p in rows],
            champion_versions=champion_versions,
        )
        result: list[CohortRow] = []
        for p in rows:
            champ = champ_scores.get(p.id)
            result.append(
                CohortRow(
                    participant_id=str(p.id),
                    trial_id=str(p.trial_id),
                    site_id=str(p.site_id),
                    # risk_score/band: denorm cache (champion-only write guard — B2c).
                    risk_score=p.risk_score,
                    risk_band=p.risk_band,
                    # top_factors/synthetic: champion participant_score row (champion-only read).
                    top_factors=list(champ.top_factors) if champ is not None else [],
                    updated_at=p.created_at,
                    synthetic=champ.synthetic if champ is not None else True,
                )
            )
        return result
