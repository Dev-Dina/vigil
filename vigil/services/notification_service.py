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
from vigil.repositories import drift as drift_repo
from vigil.repositories import scoring as scoring_repo
from vigil.repositories import users as user_repo
from vigil.repositories.session import (
    auth_lookup_session,
    platform_session,
    sponsor_bootstrap_session,
)
from vigil.services.email_sender import (
    build_drift_alert_body,
    build_notification_body,
    get_email_sender,
)
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
            log.info(
                "notify.no_recipients", extra={"extra": {"crossing_id": crossing_id}}
            )
            return {"status": "no_recipients"}

        deep_link = f"{get_settings().app_base_url.rstrip('/')}/at-risk"
        subject, body = build_notification_body(
            deep_link=deep_link, synthetic=synthetic
        )
        # Send first; flip notified ONLY on success (a send failure raises → retry, no flip).
        get_email_sender().send(to=recipients, subject=subject, body=body)
        scoring_repo.mark_crossing_notified(session, cid)
        log.info(
            "notify.sent",
            extra={
                "extra": {"crossing_id": crossing_id, "n_recipients": len(recipients)}
            },
        )
        return {"status": "sent", "n_recipients": len(recipients)}


# ---------------------------------------------------------------------------
# Gate M2 — drift-breach ALERT to the ML/platform engineer (platform-scoped)
# ---------------------------------------------------------------------------


def resolve_drift_recipients() -> list[str]:
    """Notification emails of the model-lifecycle engineer(s) — the M2 drift-alert recipients.

    The PLATFORM-SCOPED analog of :func:`resolve_recipients`. Drift is a MODEL-level statistic with
    NO participant ``(sponsor, trial, site)`` tuple, so routing is by ROLE — the ``platform_admin``
    (superset operator) AND the ``mlops_engineer`` (Gate RBAC-OPS — owns drift-run / register /
    promote), the engineers who ACT on a breach — rather than by ``scope.permits``. A site
    coordinator is NEVER a drift recipient; an auditor / llmops_engineer is not either. The address
    comes off the user record (seed/env-driven). Returns a de-duplicated, sorted list (deterministic);
    empty when no such engineer has an address set.
    """
    with auth_lookup_session() as session:
        return sorted(
            {
                user.notification_email
                for user in user_repo.drift_alert_recipients_with_notification_email(
                    session
                )
                if user.notification_email
            }
        )


def notify_drift_breach(*, drift_point_id: str) -> dict[str, object]:
    """Send the PII-free drift-breach ALERT for ONE breached anchor point, EXACTLY ONCE (Gate M2).

    Send-once is the drift point's ``notified`` flag: an already-notified point does NOT re-send;
    on a fresh breach with platform recipient(s) we send, then flip ``notified`` true — so a retried
    job never double-sends. NO recipient → sends nothing and does NOT flip ``notified`` (harmless to
    re-resolve later). A send FAILURE raises (``notified`` stays false → the Arq job retries, bounded
    by the worker ``max_tries``). The body is PII-free by construction (drift has no participant);
    recipients are PLATFORM-SCOPED (:func:`resolve_drift_recipients`), distinct from the tenant-scoped
    crossing path. Cross-run dedupe (don't re-alert an ONGOING breach) is the producer's breach EDGE.
    """
    pid = uuid.UUID(drift_point_id)
    with platform_session() as session:
        point = drift_repo.get_drift_point(session, pid)
        if point is None:
            return {"status": "not_found"}
        if point.notified:
            return {"status": "already_notified"}
        recipients = resolve_drift_recipients()
        if not recipients:
            log.info(
                "drift_notify.no_recipients",
                extra={"extra": {"drift_point_id": drift_point_id}},
            )
            return {"status": "no_recipients"}

        deep_link = f"{get_settings().app_base_url.rstrip('/')}/monitoring"
        subject, body = build_drift_alert_body(
            regime=point.regime,
            model_version=point.model_version,
            metric=point.metric,
            value=point.value,
            threshold=point.threshold,
            distribution=point.distribution,
            reference_n=point.reference_n,
            current_n=point.current_n,
            synthetic=point.synthetic,
            constructed_demo=point.constructed_demo,
            deep_link=deep_link,
        )
        # Send first; flip notified ONLY on success (a send failure raises → retry, no flip).
        get_email_sender().send(to=recipients, subject=subject, body=body)
        drift_repo.mark_drift_point_notified(session, pid)
        log.info(
            "drift_notify.sent",
            extra={
                "extra": {
                    "drift_point_id": drift_point_id,
                    "n_recipients": len(recipients),
                }
            },
        )
        return {"status": "sent", "n_recipients": len(recipients)}
