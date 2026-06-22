"""Scoped tool layer for the in-app agents (specs/rag.md § Scope propagation + § Retrieval stack).

THE KEYSTONE ISOLATION SURFACE. An agent runs as a queued (Arq) job and reaches data ONLY
through these tools, which run ONLY inside a fully-resolved, scope-bound session. Two invariants
this module exists to guarantee:

1. **Full-Scope re-resolution at job time.** ``agent_tool_context(user_id, ...)`` re-resolves the
   caller's FULL :class:`~vigil.core.scope.Scope` from ``user_id`` via ``resolve_scope`` (freshest
   grants, smallest trust surface) — it does NOT accept a pre-serialized scope or a bare
   sponsor_id. Every tool runs under ``scoped_session(scope)``. It NEVER uses
   ``sponsor_bootstrap_session`` or a raw session factory — those bind sponsor-only and would drop
   the site/trial narrowing, leaking another SITE's data within a tenant.

2. **Dual-axis enforcement.**
   - Axis 1 (cross-tenant): ``scoped_session`` sets the sponsor RLS GUC → Postgres hides other
     sponsors' rows.
   - Axis 2 (cross-site within a tenant): RLS is sponsor-only, so the structured tool ADDITIONALLY
     checks ``scope.permits(ScopeTuple(sponsor, trial, site))`` for the row — a site-scoped
     coordinator reaches only their site's participants. An out-of-scope id returns empty, never an
     error that leaks existence.

Risk facts come ONLY through the champion-allowlist surfacing (``get_surfaceable_score`` /
``champion_model_versions``) — never raw ``participant_score``, never a shadow/challenger row.

No router, no LLM, no agent reasoning here (Gate 5.5). This is the safe tool set + the scope-bound
context they run in.
"""

from __future__ import annotations

import uuid
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass

from vigil.core.scope import Scope, ScopeTuple
from vigil.repositories import documents as doc_repo
from vigil.repositories import routing as routing_repo
from vigil.repositories import scoring as scoring_repo
from vigil.repositories import tenancy as tenancy_repo
from vigil.repositories import users as user_repo
from vigil.repositories.session import (
    auth_lookup_session,
    platform_session,
    scoped_session,
)
from vigil.services.scope_resolver import resolve_scope


class AgentScopeError(Exception):
    """Raised when an agent tool context cannot be established for a user."""


@dataclass(frozen=True, slots=True)
class RiskFacts:
    participant_id: str  # the internal UUID (stable locator)
    coded_ref: (
        str | None
    )  # human-readable pseudonymous ref; identity roles ONLY (None otherwise)
    risk_score: float
    risk_band: str
    model_version: str  # the champion-of-record version (never a shadow/challenger)
    synthetic: bool
    top_factors: list[str]


@dataclass(frozen=True, slots=True)
class DocHit:
    source_ref: str
    chunk_index: int
    chunk_text: str
    distance: float


@dataclass(frozen=True, slots=True)
class CohortRiskRow:
    """One scope-visible at-risk participant for the operational TRIAGE tool (Gate OPS-ASSIST).

    Coded refs only; risk facts come through the champion allowlist (never shadow/challenger).
    ``needs_intervention`` = a high-risk participant with NO intervention logged yet. This is
    operational status (who's flagged / who has an open follow-up), NOT clinical advice.
    """

    participant_id: str
    coded_ref: str | None  # identity roles only (same gate as participant detail)
    risk_band: str
    risk_score: float
    synthetic: bool
    top_factor: str | None
    intervention_logged: bool
    needs_intervention: bool


@dataclass(slots=True)
class ToolContext:
    """The ONLY handle the tools accept. Carries the resolved full scope + its scope-bound
    session + the champion allowlist. Constructed solely by ``agent_tool_context`` — a tool
    cannot reach the DB without one, so there is no path around the scoped session."""

    scope: Scope
    session: object  # an open scoped_session(scope) transaction
    champion_versions: frozenset[str]


@contextmanager
def agent_tool_context(
    user_id: str, *, requested_sponsor: str | None = None
) -> Iterator[ToolContext]:
    """Re-resolve the caller's FULL scope from ``user_id`` and open the scope-bound tool context.

    The queued-agent chokepoint: pass ``user_id`` (and, for multi-sponsor CRO callers, the
    requested sponsor) — NOT a serialized scope. Scope is rebuilt at job time from the DB, so a
    grant change is reflected. Every tool invoked with the yielded context runs under
    ``scoped_session(scope)`` — never ``sponsor_bootstrap_session``, never a raw factory.
    """
    try:
        uid = uuid.UUID(user_id)
    except (ValueError, AttributeError) as exc:
        raise AgentScopeError(f"invalid user_id {user_id!r}") from exc

    # 1. Re-resolve the FULL scope from the DB (freshest grants), exactly like login.
    with auth_lookup_session() as s:
        user = user_repo.get_by_id(s, uid)
        if user is None:
            raise AgentScopeError(f"user {user_id} not found")
        scope = resolve_scope(s, user)

    # 2. Champion allowlist from the platform routing table (not sponsor-scoped).
    with platform_session() as s:
        champion_versions = frozenset(routing_repo.champion_model_versions(s))

    # 3. The scope-bound session every tool runs under (RLS = axis-1 cross-tenant).
    with scoped_session(scope, sponsor_id=requested_sponsor) as session:
        yield ToolContext(
            scope=scope, session=session, champion_versions=champion_versions
        )


# ---------------------------------------------------------------------------
# Tools (each takes a ToolContext; none open their own session)
# ---------------------------------------------------------------------------


def _resolve_in_scope_participant(ctx: ToolContext, ref: str):
    """Resolve a participant the caller may see from EITHER a UUID or a coded_ref (e.g. ``A-0001``).

    Scope-bound by construction and identical to every other tool read — there is NO new
    cross-tenant query and NO scope widening:
    - the lookup runs under ``ctx.session`` so Postgres RLS hides other sponsors (axis 1);
    - ``scope.permits`` then enforces the cross-site narrowing RLS can't express (axis 2).
    A ref outside the caller's scope (or unknown / malformed) returns ``None`` — never an error
    that leaks existence. coded_ref resolution is the SAME scoped read ``cohort_at_risk`` uses.
    """
    candidates: list = []
    try:
        pid = uuid.UUID(ref)
    except (ValueError, AttributeError, TypeError):
        pid = None
    if pid is not None:
        p = tenancy_repo.get_participant(ctx.session, pid)
        if p is not None:
            candidates = [p]
    else:
        # Not a UUID → treat as a coded_ref (RLS-scoped; may match >1 within a sponsor's sites).
        candidates = tenancy_repo.get_participants_by_coded_ref(ctx.session, ref)
    for p in candidates:
        # axis 2: cross-site within the tenant — RLS is sponsor-only, so enforce site/trial here.
        if ctx.scope.permits(
            ScopeTuple(
                sponsor_id=str(p.sponsor_id),
                trial_id=str(p.trial_id),
                site_id=str(p.site_id),
            )
        ):
            return p
    return None


def participant_risk_facts(ctx: ToolContext, participant_ref: str) -> RiskFacts | None:
    """Champion-only risk facts for a participant the caller may see, else ``None``.

    ``participant_ref`` is EITHER the internal UUID OR the human-readable coded_ref
    (e.g. ``A-0001``) — both resolve to the same participant WITHIN the caller's scope (dual-axis:
    RLS cross-tenant + ``scope.permits`` cross-site; see ``_resolve_in_scope_participant``). Risk
    comes through the champion allowlist — a shadow/challenger row can never be returned.
    Out-of-scope / not-found / unscored → ``None`` (no error that leaks existence). The returned
    ``coded_ref`` is surfaced ONLY to identity roles (same gate as ``cohort_at_risk`` / participant
    detail), ``None`` otherwise — resolution is scope-bound regardless of role.
    """
    from vigil.domain import IDENTITY_ROLES  # local: avoid import cycle at module load

    p = _resolve_in_scope_participant(ctx, participant_ref)
    if p is None:
        return None

    champ = scoring_repo.get_surfaceable_score(
        ctx.session, p.id, champion_versions=ctx.champion_versions
    )
    if champ is None:
        return None  # no champion score — never fall back to a non-champion row

    is_identity = ctx.scope.role in IDENTITY_ROLES
    return RiskFacts(
        participant_id=str(p.id),
        coded_ref=p.coded_ref if is_identity else None,
        risk_score=champ.risk_score,
        risk_band=champ.risk_band,
        model_version=champ.model_version,
        synthetic=champ.synthetic,
        top_factors=list(champ.top_factors),
    )


def cohort_at_risk(
    ctx: ToolContext, *, bands: tuple[str, ...] = ("high",), limit: int = 25
) -> list[CohortRiskRow]:
    """The caller's OWN scoped at-risk cohort — operational TRIAGE, never clinical advice.

    REUSES the existing scoped at-risk read path (the cohort/at-risk surface): the RLS-scoped
    ``list_participants`` (axis 1: cross-tenant) + the dual-axis ``scope.permits`` site narrowing
    (axis 2: cross-site, exactly like ``participant_risk_facts``) + the champion-only score read.
    Returns ONLY participants the caller's scope already permits — a site coordinator sees only
    their site; cross-tenant is impossible by construction (no new SQL, no scope widening, no LLM).
    A platform caller (no tenant tuples) reaches no rows. ``needs_intervention`` = a high-risk
    participant with NO intervention logged yet. Coded refs surface only for identity roles.
    """
    from vigil.domain import IDENTITY_ROLES  # local: avoid import cycle at module load

    # axis 1 (RLS): sponsor-scoped participants; axis 2: narrow to the caller's permitted site set.
    rows = [
        p
        for p in tenancy_repo.list_participants(ctx.session, risk_band=None, limit=None)
        if p.risk_band in bands
        and ctx.scope.permits(
            ScopeTuple(
                sponsor_id=str(p.sponsor_id),
                trial_id=str(p.trial_id),
                site_id=str(p.site_id),
            )
        )
    ]
    rows.sort(key=lambda p: p.risk_score, reverse=True)
    rows = rows[:limit]

    # Champion-only facts (synthetic + top factor) — a shadow/challenger row can never surface.
    champ = scoring_repo.champion_scores_by_participant(
        ctx.session, [p.id for p in rows], champion_versions=ctx.champion_versions
    )
    is_identity = ctx.scope.role in IDENTITY_ROLES
    out: list[CohortRiskRow] = []
    for p in rows:
        c = champ.get(p.id)
        logged = len(tenancy_repo.list_interventions(ctx.session, p.id)) > 0
        out.append(
            CohortRiskRow(
                participant_id=str(p.id),
                coded_ref=p.coded_ref if is_identity else None,
                risk_band=p.risk_band,
                risk_score=p.risk_score,
                synthetic=c.synthetic if c is not None else True,
                top_factor=(list(c.top_factors)[0] if c and c.top_factors else None),
                intervention_logged=logged,
                needs_intervention=(p.risk_band == "high" and not logged),
            )
        )
    return out


def search_documents(ctx: ToolContext, query: str, *, k: int = 5) -> list[DocHit]:
    """Vector retrieval over the doc corpus, scope-filtered (RLS): global cards + own-sponsor
    chunks only, never another sponsor's. Returns chunks with their source_ref for citation."""
    from vigil.agents.embeddings import (
        get_embedder,
    )  # lazy: avoid loading ST unless used

    query_embedding = get_embedder().embed(query)
    hits = doc_repo.search_chunks(ctx.session, query_embedding=query_embedding, k=k)
    return [
        DocHit(
            source_ref=h.source_ref,
            chunk_index=h.chunk_index,
            chunk_text=h.chunk_text,
            distance=h.distance,
        )
        for h in hits
    ]
