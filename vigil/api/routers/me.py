"""me router (api.md § me) — per-user self-service settings (Phase 9, Gate 9.5).

A user manages ONLY their OWN notification email — the routes take no user-id, so there is no path
to set another user's. Used by the clinical-ops loop to route the PII-free serious-risk doorbell.
"""

from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel, EmailStr

from vigil.api.deps import ScopeDep
from vigil.core.scope import Scope
from vigil.services import me_service

router = APIRouter(prefix="/me", tags=["me"])


class NotificationEmailIn(BaseModel):
    notification_email: (
        EmailStr | None
    )  # None clears it; validated as a plausible email


class NotificationEmailOut(BaseModel):
    user_id: str
    notification_email: str | None


@router.get("/notification-email", response_model=NotificationEmailOut)
async def get_notification_email(scope: Scope = ScopeDep) -> NotificationEmailOut:
    """The CALLER's own notification email (null if unset)."""
    return NotificationEmailOut(
        user_id=scope.user_id,
        notification_email=me_service.get_own_notification_email(scope),
    )


@router.put("/notification-email", response_model=NotificationEmailOut)
async def put_notification_email(
    body: NotificationEmailIn, scope: Scope = ScopeDep
) -> NotificationEmailOut:
    """Set/update/clear the CALLER's OWN notification email (no user-id param → only self)."""
    email = (
        str(body.notification_email) if body.notification_email is not None else None
    )
    stored = me_service.set_own_notification_email(scope, email)
    return NotificationEmailOut(user_id=scope.user_id, notification_email=stored)
