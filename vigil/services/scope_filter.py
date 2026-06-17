"""Cross-site-within-tenant narrowing for the direct read APIs (SEC-1).

The participant/intervention RLS policy keys on ``sponsor_id`` ONLY (FORCE, fail-closed) — it is
the cross-TENANT guarantee. But ``domain.md`` scopes site roles to their site (coordinator/PI =
"own site & trial"; CRA = assigned sites) and states RLS admits only rows whose
``(sponsor_id, trial_id, site_id)`` is in scope. RLS can't express the caller's tuple-COUPLED scope
set, so the cross-SITE narrowing is applied here at the service layer — exactly as the 5.4 agent
tools do (`vigil/agents/tools.py` → ``scope.permits``). This keeps the direct read APIs
(cohort, participant detail, /risk, /risk/history, interventions) consistent with the agent-tool
guarantee and with domain.md.

A broader role is NOT over-narrowed: sponsor-oversight (``(sponsor, *, *)``) permits every
trial/site of its sponsor; a CRA permits only its granted ``(sponsor, trial, site)`` tuples; a
site user permits only its own site. Platform callers carry no tenant tuples and reach no
participant rows through RLS anyway (no platform bypass on these tables), so this is consistent.
"""

from __future__ import annotations

import uuid

from vigil.core.scope import Scope, ScopeTuple


def participant_visible(
    scope: Scope,
    *,
    sponsor_id: uuid.UUID | str,
    trial_id: uuid.UUID | str,
    site_id: uuid.UUID | str,
) -> bool:
    """True iff a participant row's (sponsor, trial, site) is within the caller's FULL scope.

    Layered on top of sponsor RLS: RLS has already restricted the candidate rows to the bound
    sponsor; this enforces the trial/site narrowing RLS does not. Tuple-coupled via
    ``scope.permits`` so a CRA with site-1/trial-A + site-2/trial-B never matches a cross-product.
    """
    return scope.permits(
        ScopeTuple(
            sponsor_id=str(sponsor_id),
            trial_id=str(trial_id),
            site_id=str(site_id),
        )
    )
