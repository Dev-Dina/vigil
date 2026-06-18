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
from vigil.services import scope_filter


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


@dataclass(frozen=True, slots=True)
class CohortSummaryView:
    """Aggregate of the caller's SCOPED cohort (api.md § cohort / CohortSummary)."""

    total: int
    by_band: dict[str, int]  # always carries all three bands (0 when none)
    mean_risk: float


def list_cohort(
    scope: Scope,
    *,
    sponsor_id: str | None = None,
    limit: int | None = 50,
    risk_band: str | None = None,
    sort: str = "risk_desc",
) -> list[CohortRow]:
    """Scope-bound ranked cohort. ``risk_band`` filters to a band (the Phase-9 at-risk surface uses
    ``risk_band='high'``); ``sort`` orders by ``risk_score`` (``risk_desc`` default | ``risk_asc``).
    Scope narrowing (RLS + SEC-1 ``scope_filter``) is applied FIRST, so the band filter/sort only
    ever see the caller's own in-scope participants — a site coordinator never sees another site's
    at-risk rows.
    """
    # Champion allowlist from the platform routing table (not sponsor-scoped).
    with platform_session() as session:
        champion_versions = routing_repo.champion_model_versions(session)

    with scoped_session(scope, sponsor_id=sponsor_id) as session:
        # Fetch the RLS-scoped, band-filtered, risk-ordered set WITHOUT a SQL limit: the limit is
        # applied AFTER the SEC-1 cross-site narrowing below, so a site role's own at-risk rows are
        # never evicted by higher-risk rows at OTHER sites of the same sponsor (the eviction bug a
        # pre-limit caused once a sponsor exceeded the limit). The band filter in SQL keeps the
        # at-risk scan bounded to matching rows.
        rows = tenancy_repo.list_participants(session, risk_band=risk_band, limit=None)
        # SEC-1: sponsor RLS narrowed to the tenant; now narrow to the caller's trial/site scope
        # (site roles see only their site) — the cross-site guarantee RLS can't express.
        rows = [
            p
            for p in rows
            if scope_filter.participant_visible(
                scope, sponsor_id=p.sponsor_id, trial_id=p.trial_id, site_id=p.site_id
            )
        ]
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

    # Phase-9 at-risk filter + sort, applied AFTER scope narrowing (so band/sort never widen what
    # the caller may see). The band is already filtered in SQL; this is defence-in-depth. Sort by
    # risk, then cap with the caller's limit LAST — so the limit bounds the SCOPED result, never
    # the pre-scope scan (the regression that dropped a site role's own at-risk rows).
    if risk_band is not None:
        result = [r for r in result if r.risk_band == risk_band]
    result.sort(key=lambda r: r.risk_score, reverse=(sort != "risk_asc"))
    return result[:limit]


def summarize_cohort(
    scope: Scope,
    *,
    sponsor_id: str | None = None,
    trial_id: str | None = None,
    site_id: str | None = None,
) -> CohortSummaryView:
    """Counts + mean risk over the caller's SCOPED cohort (api.md § cohort / CohortSummary).

    Computed over the SAME scope-bound slice as ``list_cohort`` (RLS + SEC-1 ``scope_filter``
    narrowing) — never wider — with NO row cap (``limit=None``), so the totals reflect the whole
    scoped cohort, not a page. ``trial_id`` / ``site_id`` are an ADDITIONAL restriction applied
    on top of the already-scope-narrowed rows: an out-of-scope id simply matches nothing (empty
    summary), never widening or leaking. ``by_band`` always carries all three bands (0 when none).
    """
    rows = list_cohort(scope, sponsor_id=sponsor_id, limit=None)
    if trial_id is not None:
        rows = [r for r in rows if r.trial_id == trial_id]
    if site_id is not None:
        rows = [r for r in rows if r.site_id == site_id]

    by_band = {"high": 0, "medium": 0, "low": 0}
    for r in rows:
        if r.risk_band in by_band:
            by_band[r.risk_band] += 1
    total = len(rows)
    mean_risk = (sum(r.risk_score for r in rows) / total) if total else 0.0
    return CohortSummaryView(total=total, by_band=by_band, mean_risk=mean_risk)
