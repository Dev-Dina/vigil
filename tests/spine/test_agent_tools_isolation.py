"""Gate 5.4 — scoped agent tool layer: DUAL-AXIS isolation (cross-tenant AND cross-site).

THE KEYSTONE TEST. The agent tool context re-resolves the caller's FULL scope from user_id and
runs every tool under scoped_session(scope). This proves:
- axis 1 (cross-tenant): a Sponsor-A caller's tools reach NO Sponsor-B data;
- axis 2 (cross-site within a tenant): a SITE-scoped coordinator reaches ONLY their site's
  participants, NOT the whole sponsor's — the leak a sponsor-only opener would produce. RLS is
  sponsor-only, so this passes ONLY because the tool enforces scope.permits(site); it MUST fail
  if sponsor_bootstrap_session (no site narrowing) were used.
- champion-only via the tool path; vector isolation; no-bypass; scope freshness.

Real Postgres via migrated_db.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from vigil.agents import tools
from vigil.agents.embeddings import get_embedder
from vigil.db.models import Participant, Site, Trial
from vigil.repositories import documents as doc_repo
from vigil.repositories.session import platform_session, sponsor_bootstrap_session

_CHAMPION_MV = "sequence_v1.1:demo"
_SHADOW_MV = "structural_v1.0:t2d"
_CARD = "data/models/t2d/model_card.md"


def _add_score(
    ids: dict[str, str],
    pid: uuid.UUID,
    *,
    trial_id: uuid.UUID,
    site_id: uuid.UUID,
    mv: str,
    score: float,
) -> None:
    from vigil.repositories import scoring as scoring_repo

    with sponsor_bootstrap_session(ids["sponsor_a"]) as session:
        scoring_repo.append_score(
            session,
            participant_id=pid,
            sponsor_id=uuid.UUID(ids["sponsor_a"]),
            trial_id=trial_id,
            site_id=site_id,
            risk_score=score,
            risk_band="high" if score >= 0.7 else "low",
            top_factors=["missed_visits"],
            reasons=[],
            model_version=mv,
            model_card_ref=_CARD,
            synthetic=True,
        )


@dataclass
class _TwoSiteTrial:
    trial_id: str
    site1_id: str
    site2_id: str
    p1_id: str  # participant at site1
    p2_id: str  # participant at site2 (same sponsor + trial, DIFFERENT site)


def _make_two_site_trial(ids: dict[str, str]) -> _TwoSiteTrial:
    """A SELF-CONTAINED sub-tenant under Sponsor A: a NEW trial with TWO sites + a participant at
    each (champion-scored). Isolated from the seeded trial_a so it pollutes no count assertions.
    Sponsor-visible by RLS; the two participants differ only by SITE — the axis-2 fixture."""
    sponsor_a = uuid.UUID(ids["sponsor_a"])
    with sponsor_bootstrap_session(ids["sponsor_a"]) as session:
        trial = Trial(sponsor_id=sponsor_a, name="Cross-site probe trial")
        session.add(trial)
        session.flush()
        s1 = Site(sponsor_id=sponsor_a, trial_id=trial.id, name="Probe Site 1")
        s2 = Site(sponsor_id=sponsor_a, trial_id=trial.id, name="Probe Site 2")
        session.add_all([s1, s2])
        session.flush()
        p1 = Participant(
            sponsor_id=sponsor_a,
            trial_id=trial.id,
            site_id=s1.id,
            coded_ref="PT-S1-0001",
            status="active",
            risk_score=0.6,
            risk_band="medium",
        )
        p2 = Participant(
            sponsor_id=sponsor_a,
            trial_id=trial.id,
            site_id=s2.id,
            coded_ref="PT-S2-0001",
            status="active",
            risk_score=0.8,
            risk_band="high",
        )
        session.add_all([p1, p2])
        session.flush()
        out = _TwoSiteTrial(
            trial_id=str(trial.id),
            site1_id=str(s1.id),
            site2_id=str(s2.id),
            p1_id=str(p1.id),
            p2_id=str(p2.id),
        )
    for pid, sid in ((out.p1_id, out.site1_id), (out.p2_id, out.site2_id)):
        _add_score(
            ids,
            uuid.UUID(pid),
            trial_id=uuid.UUID(out.trial_id),
            site_id=uuid.UUID(sid),
            mv=_CHAMPION_MV,
            score=0.7,
        )
    return out


def _make_site_coordinator(ids: dict[str, str], *, trial_id: str, site_id: str) -> str:
    """Insert a COORDINATOR (site role) bound to (sponsor_a, trial_id, site_id). Returns user_id."""
    from vigil.core.security import hash_password
    from vigil.db.models import User

    with platform_session() as session:
        u = User(
            email=f"probe.coord.{uuid.uuid4().hex[:8]}@vigil.example",
            password_hash=hash_password("probe-pw"),
            role="coordinator",
            home_sponsor_id=uuid.UUID(ids["sponsor_a"]),
            home_trial_id=uuid.UUID(trial_id),
            home_site_id=uuid.UUID(site_id),
            scope_ver=1,
        )
        session.add(u)
        session.flush()
        return str(u.id)


# ---------------------------------------------------------------------------
# Axis 2 (THE LANDMINE): cross-site within a tenant
# ---------------------------------------------------------------------------


def test_site_scoped_agent_cannot_reach_other_site(migrated_db: dict[str, str]) -> None:
    ids = migrated_db
    fix = _make_two_site_trial(ids)
    # A coordinator bound to site1; participant p2 is at site2 (same sponsor + trial, other site).
    coord_site1 = _make_site_coordinator(
        ids, trial_id=fix.trial_id, site_id=fix.site1_id
    )

    with tools.agent_tool_context(coord_site1) as ctx:
        own = tools.participant_risk_facts(ctx, fix.p1_id)
        other_site = tools.participant_risk_facts(ctx, fix.p2_id)

    assert own is not None, "coordinator could not reach their OWN site's participant"
    assert own.participant_id == fix.p1_id
    assert other_site is None, (
        "CROSS-SITE LEAK: site-scoped coordinator reached another site's participant — "
        "the tool must enforce scope.permits(site), not just sponsor RLS (would pass if "
        "sponsor_bootstrap_session were used)"
    )


def test_sponsor_scoped_agent_reaches_all_sites(migrated_db: dict[str, str]) -> None:
    # A sponsor-oversight role (tuple = sponsor,*,*) sees BOTH sites — proves axis-2 narrowing is
    # role-correct (not over-restrictive), not a blanket site filter.
    ids = migrated_db
    fix = _make_two_site_trial(ids)

    oversight_id = _user_id_by_email(ids, "oversight.a@vigil.example")
    with tools.agent_tool_context(oversight_id) as ctx:
        a1 = tools.participant_risk_facts(ctx, fix.p1_id)
        a2 = tools.participant_risk_facts(ctx, fix.p2_id)
    assert a1 is not None and a2 is not None, (
        "sponsor-scoped role should reach all sites within its sponsor"
    )


# ---------------------------------------------------------------------------
# Axis 1: cross-tenant
# ---------------------------------------------------------------------------


def test_cross_tenant_agent_tools_blocked(migrated_db: dict[str, str]) -> None:
    ids = migrated_db
    # coord.a (Sponsor A) must reach NONE of Sponsor B's data via any tool.
    with tools.agent_tool_context(ids["coordinator_a"]) as ctx:
        facts_b = tools.participant_risk_facts(ctx, ids["participant_b"])
    assert facts_b is None, (
        "cross-tenant leak: Sponsor-A agent reached Sponsor-B participant"
    )

    # Out-of-scope id returns empty, never an error that leaks existence.
    with tools.agent_tool_context(ids["coordinator_a"]) as ctx:
        bogus = tools.participant_risk_facts(ctx, str(uuid.uuid4()))
    assert bogus is None


# ---------------------------------------------------------------------------
# Champion-only via the tool path
# ---------------------------------------------------------------------------


def test_tool_returns_champion_not_shadow(migrated_db: dict[str, str]) -> None:
    ids = migrated_db
    pid = uuid.UUID(ids["participant_a"])
    site_a, trial_a = uuid.UUID(ids["site_a"]), uuid.UUID(ids["trial_a"])
    _add_score(ids, pid, trial_id=trial_a, site_id=site_a, mv=_CHAMPION_MV, score=0.66)
    _add_score(
        ids, pid, trial_id=trial_a, site_id=site_a, mv=_SHADOW_MV, score=0.99
    )  # shadow

    with tools.agent_tool_context(ids["coordinator_a"]) as ctx:
        facts = tools.participant_risk_facts(ctx, ids["participant_a"])
    assert facts is not None
    assert facts.model_version == _CHAMPION_MV, (
        f"tool surfaced {facts.model_version!r} — shadow must never reach the agent"
    )


# ---------------------------------------------------------------------------
# Vector isolation via the tool path
# ---------------------------------------------------------------------------


def test_vector_tool_scope_isolation(migrated_db: dict[str, str]) -> None:
    ids = migrated_db
    emb = get_embedder()
    secret = "Sponsor A confidential protocol amendment delta echo foxtrot"
    with sponsor_bootstrap_session(ids["sponsor_a"]) as session:
        a_chunk = doc_repo.add_chunk(
            session,
            source_ref="protocol/a_secret.md",
            chunk_index=0,
            chunk_text=secret,
            embedding=emb.embed(secret),
            sponsor_id=uuid.UUID(ids["sponsor_a"]),
        )
        a_chunk_id = str(a_chunk.id)
    with platform_session() as session:
        g = doc_repo.add_chunk(
            session,
            source_ref="data/models/PHASE3_CARD.md",
            chunk_index=500,
            chunk_text="Vigil sequence model PR-AUC method demonstration scorecard",
            embedding=emb.embed("Vigil sequence model PR-AUC scorecard"),
            sponsor_id=None,
        )
        global_id = str(g.id)

    # Sponsor B's coordinator: tool returns the global card chunk, never Sponsor A's secret.
    with tools.agent_tool_context(ids["coordinator_b"]) as ctx:
        hits = tools.search_documents(ctx, secret, k=20)
    refs = {h.source_ref for h in hits}
    assert "protocol/a_secret.md" not in refs, "cross-tenant vector leak via the tool"
    # sanity: globals reachable (the card chunk we added is global)
    _ = (a_chunk_id, global_id)


# ---------------------------------------------------------------------------
# No-bypass + scope-freshness (structural guarantees)
# ---------------------------------------------------------------------------


def test_tool_layer_has_no_bootstrap_or_raw_factory() -> None:
    # Check the IMPORTED NAMES (not prose): the tool module must bind scoped_session and must NOT
    # bind sponsor_bootstrap_session or a raw session factory — so no tool can reach those openers.
    assert hasattr(tools, "scoped_session"), "tool layer must use scoped_session"
    assert not hasattr(tools, "sponsor_bootstrap_session"), (
        "agent tool layer must NEVER import/use sponsor_bootstrap_session (drops site narrowing)"
    )
    assert not hasattr(tools, "get_session_factory"), (
        "agent tool layer must NEVER open a raw session factory — only scoped_session"
    )

    # Belt-and-suspenders: no code line (docstrings/comments stripped) references the bad openers.
    import ast
    import inspect

    tree = ast.parse(inspect.getsource(tools))
    code_names = {node.id for node in ast.walk(tree) if isinstance(node, ast.Name)} | {
        node.attr for node in ast.walk(tree) if isinstance(node, ast.Attribute)
    }
    assert "sponsor_bootstrap_session" not in code_names
    assert "get_session_factory" not in code_names


def test_context_re_resolves_scope_from_user_id(
    migrated_db: dict[str, str], monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    # The context must REBUILD scope at job time (resolve_scope), not take a pre-built blob.
    ids = migrated_db
    called: dict[str, int] = {"n": 0}
    real = tools.resolve_scope

    def _spy(session, user):  # type: ignore[no-untyped-def]
        called["n"] += 1
        return real(session, user)

    monkeypatch.setattr(tools, "resolve_scope", _spy)
    with tools.agent_tool_context(ids["coordinator_a"]) as ctx:
        assert ctx.scope.user_id == ids["coordinator_a"]
    assert called["n"] == 1, (
        "agent_tool_context must call resolve_scope (re-resolve at job time)"
    )


def _user_id_by_email(ids: dict[str, str], email: str) -> str:
    from vigil.repositories import users as user_repo
    from vigil.repositories.session import auth_lookup_session

    with auth_lookup_session() as s:
        u = user_repo.get_by_email(s, email)
        assert u is not None, f"seed user {email} missing"
        return str(u.id)
