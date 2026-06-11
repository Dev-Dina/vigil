"""Routing B3 tests — drift-triggered fallback + audited manual promotion.

Covers specs/routing.md § Drift-triggered fallback, § Audited promotion, and the
safety/honesty/visibility invariants. Real Postgres via migrated_db (routing_state,
audit_log, and the audit_scope RLS policy are not mockable).

Fresh per-test regimes are used so the session-scoped DB is not disturbed across tests.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone

import pytest

from vigil.core.scope import Scope, ScopeTuple
from vigil.domain import Role

_CARD = "data/models/t2d/model_card.md"  # real, committed card path


def _platform_admin_scope(user_id: str) -> Scope:
    return Scope(
        user_id=user_id,
        role=Role.PLATFORM_ADMIN,
        home_sponsor_id=None,
        home_cro_id=None,
        tuples=(),
        scope_ver=1,
    )


def _auditor_scope(user_id: str) -> Scope:
    return Scope(
        user_id=user_id,
        role=Role.AUDITOR,
        home_sponsor_id=None,
        home_cro_id=None,
        tuples=(),
        scope_ver=1,
    )


def _sponsor_scope(sponsor_id: str) -> Scope:
    return Scope(
        user_id="00000000-0000-0000-0000-0000000000aa",
        role=Role.COORDINATOR,
        home_sponsor_id=sponsor_id,
        home_cro_id=None,
        tuples=(ScopeTuple(sponsor_id=sponsor_id),),
        scope_ver=1,
    )


def _seed_champion(regime: str, version: str, card: str = _CARD) -> uuid.UUID:
    from vigil.db.models import RoutingState
    from vigil.repositories.session import platform_session

    rid = uuid.uuid4()
    with platform_session() as session:
        session.add(
            RoutingState(
                id=rid,
                regime=regime,
                role="champion",
                model_version=version,
                model_card_ref=card,
                health="healthy",
            )
        )
        session.flush()
    return rid


def _seed_promote_audit(
    *, regime: str, from_version: str | None, to_version: str, card: str, when: datetime
) -> None:
    from vigil.db.models import AuditLog
    from vigil.repositories.session import platform_session

    with platform_session() as session:
        session.add(
            AuditLog(
                actor_user_id=None,
                action="model_promote",
                target_type="routing_state",
                target_id=str(uuid.uuid4()),
                sponsor_id=None,
                detail={
                    "from_version": from_version,
                    "to_version": to_version,
                    "regime": regime,
                    "reason": "seed",
                    "eval_provenance": "architecture_validation",
                    "model_card_ref": card,
                },
                created_at=when,
            )
        )
        session.flush()


# ---------------------------------------------------------------------------
# 1. Fallback demotes to last-known-good prior champion
# ---------------------------------------------------------------------------


def test_fallback_demotes_to_last_known_good(migrated_db: dict[str, str]) -> None:
    from vigil.repositories import routing as routing_repo
    from vigil.repositories.session import platform_session
    from vigil.services.routing_service import BreachSignal, handle_breach

    regime = "b3_rollback"
    _seed_champion(regime, "v2_breached", card="data/models/t2d/model_card.md")
    now = datetime.now(tz=timezone.utc)
    _seed_promote_audit(
        regime=regime,
        from_version=None,
        to_version="v1_good",
        card="data/models/t2d/model_card.md",
        when=now - timedelta(hours=2),
    )
    _seed_promote_audit(
        regime=regime,
        from_version="v1_good",
        to_version="v2_breached",
        card="data/models/t2d/model_card.md",
        when=now - timedelta(hours=1),
    )

    result = handle_breach(BreachSignal(regime, "v2_breached", breached=True))

    assert result.outcome == "rolled_back", result
    assert result.to_version == "v1_good"
    with platform_session() as session:
        champ = routing_repo.get_champion(session, regime=regime)
    assert champ is not None
    assert champ.model_version == "v1_good", (
        "champion not rolled back to last-known-good"
    )
    assert champ.health == "healthy"

    # An audit row records the fallback: system actor, null sponsor, correct versions.
    from sqlalchemy import select

    from vigil.db.models import AuditLog

    with platform_session() as session:
        fb = session.execute(
            select(AuditLog).where(
                AuditLog.action == "model_fallback",
                AuditLog.detail["regime"].astext == regime,
            )
        ).scalar_one()
    assert fb.actor_user_id is None, "fallback must be system-initiated (null actor)"
    assert fb.sponsor_id is None, (
        "routing audit rows are platform-scoped (null sponsor)"
    )
    assert fb.detail["from_version"] == "v2_breached"
    assert fb.detail["to_version"] == "v1_good"
    assert fb.detail["reason"] == "drift_breach"
    assert fb.detail["model_card_ref"]


# ---------------------------------------------------------------------------
# 2. Fallback with no prior champion → suspend (no auto-shadow-promotion)
# ---------------------------------------------------------------------------


def test_fallback_no_prior_suspends(migrated_db: dict[str, str]) -> None:
    from vigil.db.models import RoutingState
    from vigil.repositories import routing as routing_repo
    from vigil.repositories.session import platform_session
    from vigil.services.routing_service import BreachSignal, handle_breach

    regime = "b3_suspend"
    _seed_champion(regime, "v_only")
    # Seed a shadow too — it must NOT be promoted by a fallback.
    with platform_session() as session:
        session.add(
            RoutingState(
                id=uuid.uuid4(),
                regime=regime,
                role="shadow",
                model_version="shadow_vX",
                model_card_ref=_CARD,
                health="healthy",
            )
        )
        session.flush()

    result = handle_breach(BreachSignal(regime, "v_only", breached=True))

    assert result.outcome == "suspended", result
    with platform_session() as session:
        champ = routing_repo.get_champion(session, regime=regime)
        shadow = routing_repo.get_shadow(session, regime=regime)
    assert champ is not None
    assert champ.health == "fallback", "regime not suspended"
    assert champ.model_version == "v_only", (
        "champion changed despite no rollback target"
    )
    assert shadow is not None
    assert shadow.model_version == "shadow_vX", (
        "shadow was promoted by fallback (forbidden)"
    )

    from sqlalchemy import select

    from vigil.db.models import AuditLog

    with platform_session() as session:
        fb = session.execute(
            select(AuditLog).where(
                AuditLog.action == "model_fallback",
                AuditLog.detail["regime"].astext == regime,
            )
        ).scalar_one()
    assert fb.actor_user_id is None
    assert fb.detail["to_version"] is None


# ---------------------------------------------------------------------------
# 3. Promotion requires platform_admin (sponsor + auditor rejected)
# ---------------------------------------------------------------------------


def test_promotion_requires_platform_admin(migrated_db: dict[str, str]) -> None:
    from vigil.repositories import routing as routing_repo
    from vigil.repositories.session import platform_session
    from vigil.services.routing_service import (
        RoutingPermissionError,
        promote,
    )

    regime = "b3_promote_guard"
    _seed_champion(regime, "v_start")

    # Sponsor-role: rejected.
    with pytest.raises(RoutingPermissionError):
        promote(
            _sponsor_scope(migrated_db["sponsor_a"]),
            regime=regime,
            model_version="v_new",
            model_card_ref=_CARD,
            eval_provenance="architecture_validation",
        )
    # Auditor (platform but read-only): rejected — not platform_admin.
    with pytest.raises(RoutingPermissionError):
        promote(
            _auditor_scope(migrated_db["auditor"]),
            regime=regime,
            model_version="v_new",
            model_card_ref=_CARD,
            eval_provenance="architecture_validation",
        )

    # platform_admin: succeeds with a NON-NULL actor.
    admin_id = migrated_db["platform_admin"]
    result = promote(
        _platform_admin_scope(admin_id),
        regime=regime,
        model_version="v_new",
        model_card_ref=_CARD,
        eval_provenance="architecture_validation",
    )
    assert result.to_version == "v_new"
    assert result.actor_user_id == admin_id

    with platform_session() as session:
        champ = routing_repo.get_champion(session, regime=regime)
    assert champ is not None
    assert champ.model_version == "v_new"
    assert str(champ.promoted_by) == admin_id

    from sqlalchemy import select

    from vigil.db.models import AuditLog

    with platform_session() as session:
        pr = session.execute(
            select(AuditLog).where(
                AuditLog.action == "model_promote",
                AuditLog.detail["regime"].astext == regime,
            )
        ).scalar_one()
    assert pr.actor_user_id is not None, "promotion actor_user_id MUST NOT be null"
    assert str(pr.actor_user_id) == admin_id


# ---------------------------------------------------------------------------
# 3b. Promotion endpoint: HTTP 403 for non-admin, 201 for platform_admin
# ---------------------------------------------------------------------------


def test_promotion_endpoint_scope(migrated_db: dict[str, str]) -> None:
    import os

    from fastapi.testclient import TestClient

    from vigil.api.app import create_app
    from vigil.services.auth_service import login

    regime = "b3_promote_http"
    _seed_champion(regime, "v_http_start")

    pw = os.environ.get("VIGIL_SEED_PASSWORD", "vigil-dev-password")

    def _token(email: str) -> str:
        async def _go() -> str:
            return (await login(email, pw)).token.access_token

        return asyncio.get_event_loop().run_until_complete(_go())

    app = create_app()
    client = TestClient(app, raise_server_exceptions=False)
    body = {
        "regime": regime,
        "model_version": "v_http_new",
        "model_card_ref": _CARD,
        "eval_provenance": "architecture_validation",
    }

    # Sponsor coordinator → 403.
    r = client.post(
        "/api/v1/monitoring/models/promote",
        json=body,
        headers={"Authorization": f"Bearer {_token('coord.a@vigil.example')}"},
    )
    assert r.status_code == 403, r.text

    # platform_admin → 201.
    r = client.post(
        "/api/v1/monitoring/models/promote",
        json=body,
        headers={"Authorization": f"Bearer {_token('admin@vigil.example')}"},
    )
    assert r.status_code == 201, r.text
    assert r.json()["to_version"] == "v_http_new"
    assert r.json()["actor_user_id"] == migrated_db["platform_admin"]


# ---------------------------------------------------------------------------
# 4. Promotion honesty: synthetic eval labeled; clinical claim + null card rejected
# ---------------------------------------------------------------------------


def test_promotion_honesty_enforced(migrated_db: dict[str, str]) -> None:
    from vigil.services.routing_service import PromotionError, promote

    regime = "b3_honesty"
    _seed_champion(regime, "v_h_start")
    admin = _platform_admin_scope(migrated_db["platform_admin"])

    # A clinical-validation claim on synthetic eval is forbidden.
    with pytest.raises(PromotionError, match="clinical"):
        promote(
            admin,
            regime=regime,
            model_version="v_h_clinical",
            model_card_ref=_CARD,
            eval_provenance="clinical_validation",
        )
    # Null/empty model_card_ref is a hard error.
    with pytest.raises(PromotionError, match="model_card_ref"):
        promote(
            admin,
            regime=regime,
            model_version="v_h_nocard",
            model_card_ref="",
            eval_provenance="architecture_validation",
        )
    # The honest synthetic label succeeds.
    result = promote(
        admin,
        regime=regime,
        model_version="v_h_ok",
        model_card_ref=_CARD,
        eval_provenance="architecture_validation",
    )
    assert result.to_version == "v_h_ok"


# ---------------------------------------------------------------------------
# 5. Audit visibility: null-sponsor routing rows are platform-only readable
# ---------------------------------------------------------------------------


def test_routing_audit_visibility(migrated_db: dict[str, str]) -> None:
    from sqlalchemy import select

    from vigil.db.models import AuditLog
    from vigil.repositories.session import platform_session, scoped_session
    from vigil.services.routing_service import promote

    regime = "b3_visibility"
    _seed_champion(regime, "v_vis_start")
    promote(
        _platform_admin_scope(migrated_db["platform_admin"]),
        regime=regime,
        model_version="v_vis_new",
        model_card_ref=_CARD,
        eval_provenance="architecture_validation",
    )

    # platform session sees the model_promote row.
    with platform_session() as session:
        rows = (
            session.execute(
                select(AuditLog).where(
                    AuditLog.action == "model_promote",
                    AuditLog.detail["regime"].astext == regime,
                )
            )
            .scalars()
            .all()
        )
    assert rows, "platform_admin cannot see the routing audit row"

    # Sponsor-scoped session must NOT see null-sponsor routing rows (audit_scope policy).
    with scoped_session(_sponsor_scope(migrated_db["sponsor_a"])) as session:
        sponsor_rows = (
            session.execute(
                select(AuditLog).where(
                    AuditLog.action == "model_promote",
                    AuditLog.detail["regime"].astext == regime,
                )
            )
            .scalars()
            .all()
        )
    assert sponsor_rows == [], (
        "sponsor session can read a null-sponsor routing audit row — audit_scope leak"
    )


# ---------------------------------------------------------------------------
# 6. Nothing changes silently: every transition writes exactly one audit row
# ---------------------------------------------------------------------------


def test_every_transition_audited(migrated_db: dict[str, str]) -> None:
    from sqlalchemy import func, select

    from vigil.db.models import AuditLog
    from vigil.repositories.session import platform_session
    from vigil.services.routing_service import (
        BreachSignal,
        handle_breach,
        promote,
    )

    regime = "b3_silent"
    _seed_champion(regime, "v_s_v1")

    def _routing_audit_count() -> int:
        with platform_session() as session:
            return session.scalar(
                select(func.count())
                .select_from(AuditLog)
                .where(
                    AuditLog.target_type == "routing_state",
                    AuditLog.detail["regime"].astext == regime,
                )
            )

    base = _routing_audit_count()

    # Transition 1: a promotion.
    promote(
        _platform_admin_scope(migrated_db["platform_admin"]),
        regime=regime,
        model_version="v_s_v2",
        model_card_ref=_CARD,
        eval_provenance="architecture_validation",
    )
    after_promo = _routing_audit_count()
    assert after_promo == base + 1, "promotion did not write exactly one audit row"

    # Transition 2: a breach → fallback to v_s_v1 (the prior champion just recorded).
    handle_breach(BreachSignal(regime, "v_s_v2", breached=True))
    after_fb = _routing_audit_count()
    assert after_fb == after_promo + 1, "fallback did not write exactly one audit row"
