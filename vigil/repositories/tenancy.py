"""Sponsor / CRO / trial / site / participant / intervention repositories.

All reads/writes here run under RLS via the scope-bound session. Cross-tenant isolation is
enforced by the engine, proven by the leakage test.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.orm import Session

from vigil.db.models import Cro, Intervention, Participant, Site, Sponsor, Trial


# --- sponsor / cro (platform-created tenant roots / registry) ---
def create_sponsor(session: Session, *, name: str) -> Sponsor:
    sponsor = Sponsor(name=name)
    session.add(sponsor)
    session.flush()
    return sponsor


def get_sponsor(session: Session, sponsor_id: uuid.UUID) -> Sponsor | None:
    return session.get(Sponsor, sponsor_id)


def list_sponsors(session: Session) -> list[Sponsor]:
    return list(session.execute(select(Sponsor)).scalars())


def create_cro(session: Session, *, name: str) -> Cro:
    cro = Cro(name=name)
    session.add(cro)
    session.flush()
    return cro


# --- trial / site ---
def create_trial(session: Session, *, sponsor_id: uuid.UUID, name: str) -> Trial:
    trial = Trial(sponsor_id=sponsor_id, name=name)
    session.add(trial)
    session.flush()
    return trial


def create_site(
    session: Session, *, sponsor_id: uuid.UUID, trial_id: uuid.UUID, name: str
) -> Site:
    site = Site(sponsor_id=sponsor_id, trial_id=trial_id, name=name)
    session.add(site)
    session.flush()
    return site


def list_trials(session: Session) -> list[Trial]:
    return list(session.execute(select(Trial)).scalars())


def list_sites(session: Session) -> list[Site]:
    return list(session.execute(select(Site)).scalars())


def get_trial(session: Session, trial_id: uuid.UUID) -> Trial | None:
    """RLS-scoped trial fetch (caller's tenant only). None when RLS-hidden or nonexistent —
    so an out-of-tenant trial id is indistinguishable from "not found" and leaks nothing."""
    return session.get(Trial, trial_id)


def get_site(session: Session, site_id: uuid.UUID) -> Site | None:
    """RLS-scoped site fetch (caller's tenant only). None when RLS-hidden or nonexistent."""
    return session.get(Site, site_id)


# --- participant ---
def create_participant(
    session: Session,
    *,
    sponsor_id: uuid.UUID,
    trial_id: uuid.UUID,
    site_id: uuid.UUID,
    coded_ref: str,
    risk_score: float = 0.0,
    risk_band: str = "low",
) -> Participant:
    participant = Participant(
        sponsor_id=sponsor_id,
        trial_id=trial_id,
        site_id=site_id,
        coded_ref=coded_ref,
        risk_score=risk_score,
        risk_band=risk_band,
    )
    session.add(participant)
    session.flush()
    return participant


def get_participant(session: Session, participant_id: uuid.UUID) -> Participant | None:
    return session.get(Participant, participant_id)


def get_participants_by_coded_ref(
    session: Session, coded_ref: str
) -> list[Participant]:
    """RLS-scoped participants whose ``coded_ref`` matches (caller's tenant only).

    The ``coded_ref`` (e.g. ``A-0001``) is the human-readable pseudonymous id. This is the
    SAME scoped read as every other participant query — RLS already hides other sponsors'
    rows, so cross-tenant resolution is impossible here. coded_ref is unique within a sponsor
    in practice, but we return ALL in-scope matches so the caller never has to assume
    uniqueness (it applies the SEC-1 site narrowing and picks the permitted one). Empty list
    when none in scope — never an error that leaks existence.
    """
    return list(
        session.execute(
            select(Participant).where(Participant.coded_ref == coded_ref)
        ).scalars()
    )


def list_participants(
    session: Session,
    *,
    risk_band: str | None = None,
    limit: int | None = None,
) -> list[Participant]:
    """RLS-scoped participants, highest risk first.

    ``risk_band`` filters to a band IN SQL (so the at-risk scan returns only matching rows).
    ``limit`` is OPTIONAL and, when given, caps THIS sponsor-wide RLS read — callers that must
    apply cross-site (SEC-1) narrowing first should pass ``limit=None`` and cap the result AFTER
    narrowing, so a site role's own rows are never evicted by higher-risk rows at OTHER sites of
    the same sponsor (the cross-site narrowing RLS cannot express). Ordering by ``risk_score``
    desc keeps the most-at-risk first regardless.
    """
    stmt = select(Participant)
    if risk_band is not None:
        stmt = stmt.where(Participant.risk_band == risk_band)
    stmt = stmt.order_by(Participant.risk_score.desc())
    if limit is not None:
        stmt = stmt.limit(limit)
    return list(session.execute(stmt).scalars())


# --- intervention ---
def add_intervention(
    session: Session,
    *,
    sponsor_id: uuid.UUID,
    participant_id: uuid.UUID,
    kind: str,
    note: str,
    actor_user_id: uuid.UUID,
) -> Intervention:
    row = Intervention(
        sponsor_id=sponsor_id,
        participant_id=participant_id,
        kind=kind,
        note=note,
        actor_user_id=actor_user_id,
    )
    session.add(row)
    session.flush()
    return row


def list_interventions(
    session: Session, participant_id: uuid.UUID
) -> list[Intervention]:
    """Intervention history for a participant, newest-first (RLS-scoped)."""
    return list(
        session.execute(
            select(Intervention)
            .where(Intervention.participant_id == participant_id)
            .order_by(Intervention.created_at.desc())
        ).scalars()
    )
