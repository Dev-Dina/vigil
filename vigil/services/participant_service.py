"""Participant detail + interventions read/write path (api.md § participants).

Scope-bound: every DB access runs under the caller's RLS session, so an out-of-scope
participant is simply invisible (returns None → router 404). ``synthetic`` is sourced from the
CHAMPION participant_score row (B4 champion-only provenance); identity (coded_ref) is surfaced
ONLY for site roles (PI/coordinator), null otherwise (api.md, dashboard.md).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from vigil.core.scope import Scope, ScopeError
from vigil.db.models import AuditLog, Engagement
from vigil.domain import IDENTITY_ROLES
from vigil.repositories import routing as routing_repo
from vigil.repositories import scoring as scoring_repo
from vigil.repositories import tenancy as tenancy_repo
from vigil.repositories.session import platform_session, scoped_session
from vigil.services import scope_filter


@dataclass(frozen=True, slots=True)
class ParticipantDetailView:
    participant_id: str
    trial_id: str
    site_id: str
    status: str
    risk_score: float
    enrolled_at: datetime
    synthetic: bool
    coded_ref: str | None  # populated ONLY for site roles; None otherwise
    # Baseline covariates — SYNTHETIC literature-prior values, NOT real measurements. Gated
    # IDENTICALLY to coded_ref (site/identity roles only; None for everyone else). The ``*_imputed``
    # flags mark a literature-prior imputation (db ``*_baseline_imputed``); the read surface MUST
    # render an imputed value WITH a synthetic/imputed label, never as a bare clinical result.
    age_years: float | None = None
    age_imputed: bool = False
    hba1c_pct: float | None = None
    hba1c_imputed: bool = False
    bmi: float | None = None
    bmi_imputed: bool = False
    sex: str | None = None
    arm_type: str | None = None


@dataclass(frozen=True, slots=True)
class InterventionView:
    id: str
    participant_id: str
    kind: str
    note: str
    actor_user_id: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ParticipantCreatedView:
    participant_id: (
        str  # internal UUID (routing/keys); coded_ref is the human-facing id
    )
    coded_ref: str
    trial_id: str
    site_id: str
    enrolled_at: datetime
    synthetic: bool  # always True — intake is labeled-synthetic demo data, never a real measurement
    n_visits: int  # engagement rows persisted (the trajectory the champion will score)


# --- Intake engagement (INTAKE-1) -------------------------------------------------------------
# A new participant needs a visit trajectory the sequence model can score. Counters are DERIVED
# server-side from the attended/missed pattern (never trusted from the client), and timestamps are
# anchored BACKWARD from now so the LAST visit is strictly in the past — the same anchoring the seed
# uses (vigil/seed.py § _seed_planted_engagement), so no intake visit is ever future-dated and the
# feature-time leakage guard (assert_feature_time_before_t) can never trip on intake data.
_INTAKE_VISIT_SPACING_DAYS = (
    28  # ~monthly visits (matches the seed's _PLANT_SPACING_DAYS)
)
_INTAKE_LAST_VISIT_MARGIN_DAYS = (
    7  # the last visit lands this far before now() (strictly past)
)
# Minimal documented trajectory when the caller supplies no visits: a freshly enrolled, engaged
# participant who attended their first two visits (the model scores this low — the honest default).
_DEFAULT_INTAKE_ATTENDED: tuple[bool, ...] = (True, True)


def _build_intake_engagement_rows(
    attended: list[bool], *, anchor_now: datetime
) -> tuple[list[dict], datetime]:
    """Derive leakage-safe synthetic engagement rows from an attended/missed pattern.

    ``cumulative_missed``/``consecutive_missed`` are computed from the sequence here, so a client
    can never smuggle inconsistent counters. Returns ``(rows, enrolled_at)`` where ``enrolled_at``
    is the trajectory START (visit 0). Every row is ``synthetic=True``.
    """
    pattern: list[bool] = list(attended) if attended else list(_DEFAULT_INTAKE_ATTENDED)
    span_days = (len(pattern) - 1) * _INTAKE_VISIT_SPACING_DAYS
    start = anchor_now.replace(hour=0, minute=0, second=0, microsecond=0) - timedelta(
        days=span_days + _INTAKE_LAST_VISIT_MARGIN_DAYS
    )
    cum = cons = 0
    rows: list[dict] = []
    for visit_index, came in enumerate(pattern):
        missed = not came
        if missed:
            cum += 1
            cons += 1
        else:
            cons = 0
        rows.append(
            {
                "visit_index": visit_index,
                "visit_timestamp": start
                + timedelta(days=_INTAKE_VISIT_SPACING_DAYS * visit_index),
                "attended": came,
                "missed": missed,
                "cumulative_missed": cum,
                "consecutive_missed": cons,
            }
        )
    return rows, start


def create_participant(
    scope: Scope,
    *,
    coded_ref: str,
    trial_id: str,
    site_id: str,
    enrolled_at: datetime | None = None,
    age_years: float | None = None,
    age_imputed: bool = False,
    hba1c_pct: float | None = None,
    hba1c_imputed: bool = False,
    bmi: float | None = None,
    bmi_imputed: bool = False,
    sex: str | None = None,
    arm_type: str | None = None,
    attended: list[bool] | None = None,
) -> ParticipantCreatedView:
    """Create a participant (+ a synthetic visit trajectory) WITHIN the caller's scope.

    The sacred requirement — cross-tenant creation is impossible — holds by construction:

    - The trial + site are READ under the caller's RLS session, so an out-of-tenant trial is simply
      invisible (``None``) → ``ScopeError`` (router → 403). A caller can NEVER obtain another
      sponsor's trial row, so it can never derive another sponsor.
    - ``sponsor_id`` is DERIVED SERVER-SIDE from the trial row — never taken from the client — so a
      caller cannot smuggle another sponsor onto the new participant.
    - SEC-1 dual-axis: ``scope_filter.participant_visible`` enforces the trial/site narrowing RLS
      can't express (a site role may create ONLY at its own ``(sponsor, trial, site)``) → 403.
    - The INSERT runs through the scoped session, so Postgres RLS ``WITH CHECK`` is the
      defense-in-depth backstop: a ``sponsor_id`` that doesn't match the bound sponsor is rejected
      by the engine, not by app code.

    Raises ``ScopeError`` (→ 403 scope_denied) when the trial/site is outside scope, and
    ``ValueError`` (→ 422) when an id is malformed or ``coded_ref`` is empty. On success writes an
    ``audit_log`` row and returns the created view. Engagement rows are ``synthetic=True`` (demo
    data, never a real measurement); each imputed-capable covariate value carries its companion
    ``*_baseline_imputed`` provenance flag (scoring.md § Covariate provenance invariant).
    """
    try:
        tid = uuid.UUID(trial_id)
        sid = uuid.UUID(site_id)
    except (ValueError, AttributeError) as exc:
        raise ValueError("trial_id and site_id must be valid UUIDs") from exc
    ref = (coded_ref or "").strip()
    if not ref:
        raise ValueError("coded_ref is required")

    actor = uuid.UUID(scope.user_id)
    # Site roles carry exactly one sponsor tuple → bind RLS to it unambiguously.
    requested_sponsor = next(iter(scope.sponsor_ids), None)

    rows, default_enrolled = _build_intake_engagement_rows(
        attended or [], anchor_now=datetime.now(tz=timezone.utc)
    )

    with scoped_session(scope, sponsor_id=requested_sponsor) as session:
        trial = tenancy_repo.get_trial(session, tid)
        site = tenancy_repo.get_site(session, sid)
        # Out-of-tenant (RLS-hidden) trial/site, or a site that is not part of this trial → denied.
        if trial is None or site is None or site.trial_id != tid:
            raise ScopeError(
                "scope_denied: cannot create a participant in a trial/site outside your scope"
            )
        sponsor_id = trial.sponsor_id  # SERVER-DERIVED — never from the client body.
        # SEC-1 cross-site narrowing: a site role may create ONLY at its own (sponsor, trial, site).
        if not scope_filter.participant_visible(
            scope, sponsor_id=sponsor_id, trial_id=tid, site_id=sid
        ):
            raise ScopeError(
                "scope_denied: cannot create a participant outside your site scope"
            )

        participant = tenancy_repo.create_participant(
            session,
            sponsor_id=sponsor_id,
            trial_id=tid,
            site_id=sid,
            coded_ref=ref,
        )
        # enrolled_at: honor the caller's assertion, else the trajectory start (visit 0).
        participant.enrolled_at = enrolled_at or default_enrolled
        # Covariates + provenance flags. A companion ``*_baseline_imputed`` is set ONLY when a value
        # is present (an absent covariate needs no provenance flag — scoring.md invariant).
        if age_years is not None:
            participant.age_years = age_years
            participant.age_years_baseline_imputed = bool(age_imputed)
        if hba1c_pct is not None:
            participant.hba1c_pct = hba1c_pct
            participant.hba1c_pct_baseline_imputed = bool(hba1c_imputed)
        if bmi is not None:
            participant.bmi = bmi
            participant.bmi_baseline_imputed = bool(bmi_imputed)
        participant.sex = sex
        participant.arm_type = arm_type
        session.flush()

        for r in rows:
            session.add(
                Engagement(
                    sponsor_id=sponsor_id,
                    participant_id=participant.id,
                    trial_id=tid,
                    site_id=sid,
                    synthetic=True,  # intake is demo data — never implies a real measurement
                    **r,
                )
            )
        session.add(
            AuditLog(
                actor_user_id=actor,
                action="participant_created",
                target_type="participant",
                target_id=str(participant.id),
                sponsor_id=sponsor_id,
                detail={
                    "coded_ref": ref,
                    "trial_id": str(tid),
                    "site_id": str(sid),
                    "n_visits": len(rows),
                    "synthetic": True,
                },
            )
        )
        session.flush()
        return ParticipantCreatedView(
            participant_id=str(participant.id),
            coded_ref=ref,
            trial_id=str(tid),
            site_id=str(sid),
            enrolled_at=participant.enrolled_at,
            synthetic=True,
            n_visits=len(rows),
        )


def get_detail(scope: Scope, participant_id: str) -> ParticipantDetailView | None:
    """Real ParticipantDetail for an in-scope participant, or None (out-of-scope/not found)."""
    try:
        pid = uuid.UUID(participant_id)
    except (ValueError, AttributeError):
        return None

    with platform_session() as session:
        champion_versions = routing_repo.champion_model_versions(session)

    with scoped_session(scope) as session:
        p = tenancy_repo.get_participant(session, pid)
        if p is None:
            return None  # RLS-hidden or nonexistent → fail closed (404)
        # SEC-1: cross-site narrowing the sponsor-only RLS can't do (domain.md; matches 5.4 tools).
        if not scope_filter.participant_visible(
            scope, sponsor_id=p.sponsor_id, trial_id=p.trial_id, site_id=p.site_id
        ):
            return None  # out of the caller's trial/site scope → fail closed (404)
        champ = scoring_repo.get_surfaceable_score(
            session, pid, champion_versions=champion_versions
        )
    # synthetic from the champion score (B4); safe-synthetic default if unscored (never claims real).
    synthetic = champ.synthetic if champ is not None else True
    is_identity = scope.role in IDENTITY_ROLES
    coded_ref = p.coded_ref if is_identity else None
    return ParticipantDetailView(
        participant_id=str(p.id),
        trial_id=str(p.trial_id),
        site_id=str(p.site_id),
        status=p.status,
        risk_score=p.risk_score,
        enrolled_at=p.enrolled_at,
        synthetic=synthetic,
        coded_ref=coded_ref,
        # Same gate as coded_ref: baseline covariates only for identity roles, None otherwise.
        age_years=p.age_years if is_identity else None,
        age_imputed=bool(p.age_years_baseline_imputed) if is_identity else False,
        hba1c_pct=p.hba1c_pct if is_identity else None,
        hba1c_imputed=bool(p.hba1c_pct_baseline_imputed) if is_identity else False,
        bmi=p.bmi if is_identity else None,
        bmi_imputed=bool(p.bmi_baseline_imputed) if is_identity else False,
        sex=p.sex if is_identity else None,
        arm_type=p.arm_type if is_identity else None,
    )


def log_intervention(
    scope: Scope, participant_id: str, *, kind: str, note: str
) -> InterventionView | None:
    """Append a real, audited intervention; the actor is the AUTHENTICATED user (server-side).

    Returns None if the participant is not visible to the caller (out-of-scope → 404). Writes
    an audit_log row alongside the intervention (nothing changes silently).
    """
    try:
        pid = uuid.UUID(participant_id)
    except (ValueError, AttributeError):
        return None
    actor = uuid.UUID(scope.user_id)
    requested_sponsor = next(iter(scope.sponsor_ids), None)

    with scoped_session(scope, sponsor_id=requested_sponsor) as session:
        p = tenancy_repo.get_participant(session, pid)
        if p is None:
            return None  # out-of-scope / not found → fail closed
        # SEC-1: cannot log an intervention on a participant outside the caller's trial/site scope.
        if not scope_filter.participant_visible(
            scope, sponsor_id=p.sponsor_id, trial_id=p.trial_id, site_id=p.site_id
        ):
            return None
        row = tenancy_repo.add_intervention(
            session,
            sponsor_id=p.sponsor_id,
            participant_id=pid,
            kind=kind,
            note=note,
            actor_user_id=actor,
        )
        session.add(
            AuditLog(
                actor_user_id=actor,
                action="intervention_logged",
                target_type="intervention",
                target_id=str(row.id),
                sponsor_id=p.sponsor_id,
                detail={"participant_id": str(pid), "kind": kind},
            )
        )
        session.flush()
        return InterventionView(
            id=str(row.id),
            participant_id=str(pid),
            kind=row.kind,
            note=row.note,
            actor_user_id=str(row.actor_user_id),
            created_at=row.created_at,
        )


def list_interventions(
    scope: Scope, participant_id: str
) -> list[InterventionView] | None:
    """Intervention history for an in-scope participant (newest-first), or None if not visible."""
    try:
        pid = uuid.UUID(participant_id)
    except (ValueError, AttributeError):
        return None
    with scoped_session(scope) as session:
        p = tenancy_repo.get_participant(session, pid)
        if p is None:
            return None
        # SEC-1: cross-site narrowing (domain.md; matches 5.4 tools) — out-of-site → fail closed.
        if not scope_filter.participant_visible(
            scope, sponsor_id=p.sponsor_id, trial_id=p.trial_id, site_id=p.site_id
        ):
            return None
        rows = tenancy_repo.list_interventions(session, pid)
        return [
            InterventionView(
                id=str(r.id),
                participant_id=str(r.participant_id),
                kind=r.kind,
                note=r.note,
                actor_user_id=str(r.actor_user_id),
                created_at=r.created_at,
            )
            for r in rows
        ]
