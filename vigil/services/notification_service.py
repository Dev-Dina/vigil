"""Scope-bound recipient resolution + the PII-free email send for serious-risk crossings (Phase 9).

A serious-risk crossing routes ONLY to the notification_email of user(s) whose SCOPE COVERS the
crossing's participant ``(sponsor_id, trial_id, site_id)`` — decided by the SAME ``scope.permits``
/ ``ScopeTuple.contains`` primitive that guards every participant read (SEC-1). A user scoped to a
DIFFERENT site is EXCLUDED, and a different-sponsor user is EXCLUDED — because even a PII-free
email mis-routed to the wrong site leaks the EXISTENCE of an at-risk participant there
(domain.md § Notification routing, isolation.md § Phase 9).

Gate 9.5 = :func:`resolve_recipients` (no email sent). Gate 9.6 = :func:`notify_crossing`: send the
PII-free doorbell to those scope-bound recipients EXACTLY ONCE per crossing (the ``notified`` flag),
from an Arq job (never inline). The send re-proves the SACRED scope-bound routing at the send path.
"""

from __future__ import annotations

import uuid

from vigil.core.config import get_settings
from vigil.core.logging import get_logger
from vigil.core.scope import ScopeError, ScopeTuple
from vigil.db.models import RiskCrossing
from vigil.repositories import scoring as scoring_repo
from vigil.repositories import users as user_repo
from vigil.repositories.session import auth_lookup_session, sponsor_bootstrap_session
from vigil.services.email_sender import build_notification_body, get_email_sender
from vigil.services.scope_resolver import resolve_scope

log = get_logger("vigil.services.notification_service")


def resolve_recipients(crossing: RiskCrossing) -> list[str]:
    """Return the notification emails of users whose scope covers the crossing's participant.

    Scope-bound by construction: each candidate user's FULL resolved scope is tested with
    ``scope.permits`` over the crossing's exact ``(sponsor, trial, site)`` tuple. Cross-site and
    cross-tenant users never match (``ScopeTuple.contains`` requires sponsor match + trial/site
    containment); platform users carry no tuples and never match. Users with no notification_email
    are not candidates. Returns a de-duplicated, sorted list (deterministic).
    """
    target = ScopeTuple(
        sponsor_id=str(crossing.sponsor_id),
        trial_id=str(crossing.trial_id),
        site_id=str(crossing.site_id),
    )

    recipients: set[str] = set()
    # is_platform read of the cross-tenant user/grant tables (no participant data touched). The
    # scope-narrowing is the scope.permits check below, NOT this read.
    with auth_lookup_session() as session:
        for user in user_repo.users_with_notification_email(session):
            try:
                scope = resolve_scope(session, user)
            except ScopeError:
                continue  # an unresolvable scope is never a recipient (fail-closed)
            if scope.permits(target) and user.notification_email:
                recipients.add(user.notification_email)
    return sorted(recipients)


def notify_crossing(*, crossing_id: str, sponsor_id: str) -> dict[str, object]:
    """Send the PII-free doorbell for a crossing to its scope-bound recipients, EXACTLY ONCE.

    Send-once is the ``notified`` flag: a crossing already notified does NOT re-send; on a fresh
    crossing with in-scope recipient(s) we send, then flip ``notified`` true — so a re-fire / a
    retried job never double-sends. A crossing with NO in-scope recipient sends nothing and does
    NOT flip ``notified`` (no one to tell; harmless to re-resolve later). A send FAILURE raises
    (``notified`` stays false → the Arq job retries, bounded by the worker ``max_tries``).

    Scope-bound (SACRED) at the SEND path: recipients come ONLY from :func:`resolve_recipients`
    (``scope.permits`` over the crossing's exact site), so a different-site / different-sponsor user
    is NEVER a recipient. The body is PII-free by construction (:func:`build_notification_body`).
    """
    cid = uuid.UUID(crossing_id)
    with sponsor_bootstrap_session(sponsor_id) as session:
        crossing = scoring_repo.get_crossing(session, cid)
        if crossing is None:
            return {"status": "not_found"}
        if crossing.notified:
            return {"status": "already_notified"}
        synthetic = bool(crossing.synthetic)
        recipients = resolve_recipients(crossing)
        if not recipients:
            log.info("notify.no_recipients", extra={"extra": {"crossing_id": crossing_id}})
            return {"status": "no_recipients"}

        deep_link = f"{get_settings().app_base_url.rstrip('/')}/at-risk"
        subject, body = build_notification_body(deep_link=deep_link, synthetic=synthetic)
        # Send first; flip notified ONLY on success (a send failure raises → retry, no flip).
        get_email_sender().send(to=recipients, subject=subject, body=body)
        scoring_repo.mark_crossing_notified(session, cid)
        log.info(
            "notify.sent",
            extra={"extra": {"crossing_id": crossing_id, "n_recipients": len(recipients)}},
        )
        return {"status": "sent", "n_recipients": len(recipients)}
