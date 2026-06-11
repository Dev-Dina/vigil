"""Per-participant risk read path (api.md ``GET /participants/{id}/risk``).

Champion-only surfacing (specs/routing.md § Champion / challenger / shadow, layer 2):
the clinical risk endpoint MUST return champion-sourced scores only. This service
resolves the champion allowlist from ``routing_state`` (platform table) and reads the
participant's score under an RLS-scoped session filtered to that allowlist. A shadow or
challenger ``participant_score`` row can never be surfaced — the filter is applied in the
repository query, not asserted after the fact. If no champion row exists the service
returns ``None`` (the router fails closed with 404), never a non-champion fallback.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from vigil.core.scope import Scope
from vigil.repositories import routing as routing_repo
from vigil.repositories import scoring as scoring_repo
from vigil.repositories.session import platform_session, scoped_session


@dataclass(frozen=True, slots=True)
class FactorView:
    feature: str
    contribution: float


@dataclass(frozen=True, slots=True)
class RiskView:
    participant_id: str
    risk_score: float
    factors: list[FactorView]
    model_version: str


def _factors_from_reasons(reasons: object) -> list[FactorView]:
    """Best-effort map of stored ``reasons`` JSON to signed factor contributions.

    Only entries shaped ``{"feature": str, "contribution": number}`` are surfaced;
    anything else is ignored (no fabricated contributions).
    """
    out: list[FactorView] = []
    if isinstance(reasons, list):
        for r in reasons:
            if (
                isinstance(r, dict)
                and isinstance(r.get("feature"), str)
                and isinstance(r.get("contribution"), (int, float))
            ):
                out.append(
                    FactorView(
                        feature=r["feature"], contribution=float(r["contribution"])
                    )
                )
    return out


def get_participant_risk(scope: Scope, participant_id: str) -> RiskView | None:
    """Return the champion-only risk view for a participant, or ``None`` (fail closed).

    ``None`` is returned when the participant id is not a UUID, when no champion is
    registered, or when the participant has no champion ``participant_score`` row.
    """
    try:
        pid = uuid.UUID(participant_id)
    except (ValueError, AttributeError):
        return None

    # Champion allowlist comes from the platform routing table (not sponsor-scoped).
    with platform_session() as session:
        champion_versions = routing_repo.champion_model_versions(session)
    if not champion_versions:
        return None  # fail closed: no champion → nothing surfaceable

    # The score read runs under the caller's RLS scope; the allowlist filter is
    # applied in the repository query so a shadow/challenger row cannot be returned.
    with scoped_session(scope) as session:
        row = scoring_repo.get_surfaceable_score(
            session, pid, champion_versions=champion_versions
        )
    if row is None:
        return None

    # Defence in depth: the query already excludes non-champion rows; assert it.
    if row.model_version not in champion_versions:  # pragma: no cover - unreachable
        raise RuntimeError(
            f"champion-only surfacing breached: {row.model_version} not a champion"
        )

    return RiskView(
        participant_id=str(pid),
        risk_score=row.risk_score,
        factors=_factors_from_reasons(row.reasons),
        model_version=row.model_version,
    )
