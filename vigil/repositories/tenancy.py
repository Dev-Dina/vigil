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


def list_participants(session: Session, limit: int = 50) -> list[Participant]:
    return list(
        session.execute(
            select(Participant).order_by(Participant.risk_score.desc()).limit(limit)
        ).scalars()
    )


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
