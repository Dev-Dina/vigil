"""Scope-bound recipient resolution for serious-risk crossings (Phase 9, Gate 9.5) — SACRED.

A serious-risk crossing routes ONLY to the notification_email of user(s) whose SCOPE COVERS the
crossing's participant ``(sponsor_id, trial_id, site_id)`` — decided by the SAME ``scope.permits``
/ ``ScopeTuple.contains`` primitive that guards every participant read (SEC-1). A user scoped to a
DIFFERENT site is EXCLUDED, and a different-sponsor user is EXCLUDED — because even a PII-free
email mis-routed to the wrong site leaks the EXISTENCE of an at-risk participant there
(domain.md § Notification routing, isolation.md § Phase 9).

No email is SENT here — this returns the recipient set for the Gate 9.6 SMTP notifier.
"""

from __future__ import annotations

from vigil.core.scope import ScopeTuple
from vigil.db.models import RiskCrossing
from vigil.repositories import users as user_repo
from vigil.repositories.session import auth_lookup_session
from vigil.services.scope_resolver import resolve_scope
from vigil.core.scope import ScopeError


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
