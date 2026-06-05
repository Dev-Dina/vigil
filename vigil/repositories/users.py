"""User + assignment-grant + audit repositories. The only DB access for these tables.

Every method takes an open, scope-bound session (from :mod:`vigil.repositories.session`);
none open their own. RLS on the session decides visibility — these methods never add a
sponsor WHERE clause to compensate (defence in depth: the engine is the backstop).
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from vigil.db.models import AssignmentGrant, AuditLog, User


def get_by_email(session: Session, email: str) -> User | None:
    return session.execute(select(User).where(User.email == email)).scalar_one_or_none()


def get_by_id(session: Session, user_id: uuid.UUID) -> User | None:
    return session.get(User, user_id)


def list_grants(session: Session, user_id: uuid.UUID) -> list[AssignmentGrant]:
    return list(
        session.execute(
            select(AssignmentGrant).where(AssignmentGrant.user_id == user_id)
        ).scalars()
    )


def list_users(session: Session, limit: int = 50) -> list[User]:
    return list(session.execute(select(User).limit(limit)).scalars())


def create_user(
    session: Session,
    *,
    email: str,
    password_hash: str,
    role: str,
    home_sponsor_id: uuid.UUID | None,
    home_cro_id: uuid.UUID | None,
    home_trial_id: uuid.UUID | None = None,
    home_site_id: uuid.UUID | None = None,
) -> User:
    user = User(
        email=email,
        password_hash=password_hash,
        role=role,
        home_sponsor_id=home_sponsor_id,
        home_cro_id=home_cro_id,
        home_trial_id=home_trial_id,
        home_site_id=home_site_id,
    )
    session.add(user)
    session.flush()
    return user


def add_grant(
    session: Session,
    *,
    user_id: uuid.UUID,
    sponsor_id: uuid.UUID,
    trial_id: uuid.UUID | None,
    site_id: uuid.UUID | None,
    granted_by: uuid.UUID,
) -> AssignmentGrant:
    grant = AssignmentGrant(
        user_id=user_id,
        sponsor_id=sponsor_id,
        trial_id=trial_id,
        site_id=site_id,
        granted_by=granted_by,
    )
    session.add(grant)
    session.flush()
    return grant


def get_grant(session: Session, grant_id: uuid.UUID) -> AssignmentGrant | None:
    return session.get(AssignmentGrant, grant_id)


def delete_grant(session: Session, grant: AssignmentGrant) -> None:
    session.delete(grant)


def bump_scope_ver(session: Session, user: User) -> None:
    user.scope_ver = user.scope_ver + 1
    session.flush()


def write_audit(
    session: Session,
    *,
    actor_user_id: uuid.UUID | None,
    action: str,
    target_type: str,
    target_id: str | None,
    sponsor_id: uuid.UUID | None,
    detail: dict | None = None,
) -> AuditLog:
    row = AuditLog(
        actor_user_id=actor_user_id,
        action=action,
        target_type=target_type,
        target_id=target_id,
        sponsor_id=sponsor_id,
        detail=detail or {},
    )
    session.add(row)
    session.flush()
    return row


def list_audit(session: Session, limit: int = 100) -> list[AuditLog]:
    return list(
        session.execute(
            select(AuditLog).order_by(AuditLog.created_at.desc()).limit(limit)
        ).scalars()
    )


def _utcnow() -> datetime:
    from datetime import UTC

    return datetime.now(UTC)
