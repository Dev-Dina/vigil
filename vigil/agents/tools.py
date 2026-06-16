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
    participant_id: str
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


def participant_risk_facts(ctx: ToolContext, participant_id: str) -> RiskFacts | None:
    """Champion-only risk facts for a participant the caller may see, else ``None``.

    Dual-axis: ``scoped_session`` (RLS) hides other sponsors (axis 1); ``scope.permits`` on the
    row's (sponsor, trial, site) hides other SITES within the tenant for a site-scoped caller
    (axis 2). Risk comes through the champion allowlist — a shadow/challenger row can never be
    returned. Out-of-scope / not-found / unscored → ``None`` (no error that leaks existence).
    """
    try:
        pid = uuid.UUID(participant_id)
    except (ValueError, AttributeError):
        return None

    p = tenancy_repo.get_participant(ctx.session, pid)
    if p is None:
        return None  # axis 1: cross-tenant / not found (RLS-hidden)

    # axis 2: cross-site within the tenant — RLS is sponsor-only, so enforce site/trial here.
    if not ctx.scope.permits(
        ScopeTuple(
            sponsor_id=str(p.sponsor_id),
            trial_id=str(p.trial_id),
            site_id=str(p.site_id),
        )
    ):
        return None

    champ = scoring_repo.get_surfaceable_score(
        ctx.session, pid, champion_versions=ctx.champion_versions
    )
    if champ is None:
        return None  # no champion score — never fall back to a non-champion row

    return RiskFacts(
        participant_id=str(pid),
        risk_score=champ.risk_score,
        risk_band=champ.risk_band,
        model_version=champ.model_version,
        synthetic=champ.synthetic,
        top_factors=list(champ.top_factors),
    )


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
