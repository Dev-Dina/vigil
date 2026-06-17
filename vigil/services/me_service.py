"""Self-service identity settings for the authenticated caller (Gate 9.5 /me).

A user manages ONLY their own ``notification_email`` (the /me endpoints take no user-id). Reads
and writes run under :func:`user_self_session` constrained strictly to ``scope.user_id`` — never
another user's row.
"""

from __future__ import annotations

import uuid

from vigil.core.scope import Scope
from vigil.repositories import users as user_repo
from vigil.repositories.session import user_self_session


def get_own_notification_email(scope: Scope) -> str | None:
    with user_self_session() as session:
        user = user_repo.get_by_id(session, uuid.UUID(scope.user_id))
        return user.notification_email if user is not None else None


def set_own_notification_email(scope: Scope, email: str | None) -> str | None:
    """Set/clear the caller's OWN notification email; returns the stored value."""
    with user_self_session() as session:
        user = user_repo.set_notification_email(
            session, uuid.UUID(scope.user_id), email
        )
        return user.notification_email if user is not None else None
