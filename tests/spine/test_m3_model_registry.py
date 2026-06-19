"""Gate M3 — model registry / register-validated-weights → GOVERNED promotion.

The FINAL step of the human-in-the-loop MLOps loop (M1 detect + M2 alert done). Proves:

- A platform engineer REGISTERS a new version (challenger/shadow) with the SUPPLIED offline
  validation metrics + provenance (recorded, never computed); it enters the catalog + routing
  projection but is NOT auto-champion and does NOT surface to clinical reads (champion-only).
- A GOVERNED promote of a registered version makes it champion, RETAINS the prior champion
  (retired in the catalog → reversible), is AUDITED (who/when/from→to), and GET /monitoring/models
  reflects it. Re-promoting the prior version rolls back.
- Honesty (fail loud): missing metrics / clinical provenance / register-as-champion / duplicate are
  rejected; a demonstration registration is labelled.
- Role/scope: register + promote are platform_admin-only (tenant roles 403); the registry read is
  platform/auditor-only.

Real Postgres via migrated_db (routing_state / model_registry / audit_log + RLS are not mockable).
Fresh per-test regimes so the session-scoped DB is not disturbed across tests.
"""

from __future__ import annotations

import os
import uuid

import pytest
from fastapi.testclient import TestClient

from vigil.api.app import create_app
from vigil.core.scope import Scope, ScopeTuple
from vigil.domain import Role
from vigil.repositories import registry as registry_repo
from vigil.repositories import routing as routing_repo
from vigil.repositories.session import platform_session
from vigil.services import routing_service
from vigil.services.routing_service import RegistrationError, RoutingPermissionError

_CARD = "data/models/t2d/model_card.md"  # real, committed card path
_ARTIFACT = (
    "data/models/t2d/seq_v2_demo.pt"  # artifact reference (weights live offline)
)


def _platform_admin_scope(user_id: str) -> Scope:
    return Scope(
        user_id=user_id,
        role=Role.PLATFORM_ADMIN,
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


def _seed_champion(regime: str, version: str, card: str = _CARD) -> None:
    from vigil.db.models import RoutingState

    with platform_session() as session:
        session.add(
            RoutingState(
                id=uuid.uuid4(),
                regime=regime,
                role="champion",
                model_version=version,
                model_card_ref=card,
                health="healthy",
            )
        )
        session.flush()


def _client() -> TestClient:
    return TestClient(create_app(), raise_server_exceptions=False)


def _token(client: TestClient, email: str) -> str:
    pw = os.environ.get("VIGIL_SEED_PASSWORD", "vigil-dev-password")
    r = client.post("/api/v1/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# 1. Register records SUPPLIED metrics/provenance, reflects as challenger, does NOT surface
# ---------------------------------------------------------------------------


def test_register_records_metrics_and_does_not_surface(
    migrated_db: dict[str, str],
) -> None:
    ids = migrated_db
    regime = "m3_register"
    admin = _platform_admin_scope(ids["platform_admin"])
    metrics = {"auc": 0.78, "brier": 0.16, "n_val": 1200}

    res = routing_service.register_model(
        admin,
        regime=regime,
        model_version="seq_v2:demo",
        artifact_ref=_ARTIFACT,
        model_card_ref=_CARD,
        metrics=metrics,
        provenance="architecture_validation",
        status="challenger",
        notes="offline-validated on the synthetic VAL fold",
    )
    assert res.status == "challenger"
    assert res.actor_user_id == ids["platform_admin"]

    with platform_session() as session:
        reg = registry_repo.get_registration(
            session, regime=regime, model_version="seq_v2:demo"
        )
        champ_versions = routing_repo.champion_model_versions(session)
        challenger = routing_repo.list_routing_state(session)
    assert reg is not None
    assert reg.metrics == metrics, (
        "the SUPPLIED metrics must be recorded verbatim, not computed"
    )
    assert reg.provenance == "architecture_validation"
    assert reg.artifact_ref == _ARTIFACT
    # Champion-only surfacing: a registered challenger is NEVER in the champion allowlist.
    assert "seq_v2:demo" not in champ_versions, (
        "a challenger must not reach the champion allowlist (champion-only surfacing)"
    )
    # Reflected in the routing projection as a challenger (visible to GET /monitoring/models).
    assert any(
        r.regime == regime
        and r.role == "challenger"
        and r.model_version == "seq_v2:demo"
        for r in challenger
    )

    # Registration is audited (who/what/when), actor non-null, sponsor null (platform).
    from sqlalchemy import select

    from vigil.db.models import AuditLog

    with platform_session() as session:
        au = session.execute(
            select(AuditLog).where(
                AuditLog.action == "model_register",
                AuditLog.detail["regime"].astext == regime,
            )
        ).scalar_one()
    assert (
        au.actor_user_id is not None and str(au.actor_user_id) == ids["platform_admin"]
    )
    assert au.sponsor_id is None
    assert au.detail["to_version"] == "seq_v2:demo"


# ---------------------------------------------------------------------------
# 2. Register + promote are platform_admin-only (tenant roles 403)
# ---------------------------------------------------------------------------


def test_register_and_promote_forbidden_for_tenant_roles(
    migrated_db: dict[str, str],
) -> None:
    regime = "m3_scope"
    _seed_champion(regime, "v_scope_start")
    client = _client()
    coord = _h(_token(client, "coord.a@vigil.example"))
    admin = _h(_token(client, "admin@vigil.example"))

    reg_body = {
        "regime": regime,
        "model_version": "v_scope_new",
        "artifact_ref": _ARTIFACT,
        "model_card_ref": _CARD,
        "metrics": {"auc": 0.75},
        "provenance": "architecture_validation",
    }
    promo_body = {
        "regime": regime,
        "model_version": "v_scope_new",
        "model_card_ref": _CARD,
        "eval_provenance": "architecture_validation",
    }

    # Tenant coordinator → 403 on both register and promote.
    assert (
        client.post(
            "/api/v1/monitoring/models/register", json=reg_body, headers=coord
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/monitoring/models/promote", json=promo_body, headers=coord
        ).status_code
        == 403
    )

    # platform_admin → 201 register, then 201 promote.
    r = client.post("/api/v1/monitoring/models/register", json=reg_body, headers=admin)
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "challenger"
    r = client.post("/api/v1/monitoring/models/promote", json=promo_body, headers=admin)
    assert r.status_code == 201, r.text
    assert r.json()["to_version"] == "v_scope_new"


# ---------------------------------------------------------------------------
# 3. Governed promote → champion, RETAINS old (reversible), audited, reflected; rollback works
# ---------------------------------------------------------------------------


def test_governed_promote_retains_old_champion_and_is_reversible(
    migrated_db: dict[str, str],
) -> None:
    ids = migrated_db
    regime = "m3_promote"
    _seed_champion(regime, "v0_seed")
    admin = _platform_admin_scope(ids["platform_admin"])

    # Register + promote A, then register + promote B.
    routing_service.register_model(
        admin,
        regime=regime,
        model_version="vA:demo",
        artifact_ref=_ARTIFACT,
        model_card_ref=_CARD,
        metrics={"auc": 0.74},
        provenance="architecture_validation",
    )
    routing_service.promote(
        admin,
        regime=regime,
        model_version="vA:demo",
        model_card_ref=_CARD,
        eval_provenance="architecture_validation",
    )
    routing_service.register_model(
        admin,
        regime=regime,
        model_version="vB:demo",
        artifact_ref=_ARTIFACT,
        model_card_ref=_CARD,
        metrics={"auc": 0.81},
        provenance="architecture_validation",
    )
    res = routing_service.promote(
        admin,
        regime=regime,
        model_version="vB:demo",
        model_card_ref=_CARD,
        eval_provenance="architecture_validation",
    )
    assert res.from_version == "vA:demo", (
        "the prior champion must be recorded (reversible)"
    )

    with platform_session() as session:
        champ = routing_repo.get_champion(session, regime=regime)
        reg_a = registry_repo.get_registration(
            session, regime=regime, model_version="vA:demo"
        )
        reg_b = registry_repo.get_registration(
            session, regime=regime, model_version="vB:demo"
        )
        champ_versions = routing_repo.champion_model_versions(session)
    assert champ is not None and champ.model_version == "vB:demo"
    assert reg_b.status == "champion"
    assert reg_a.status == "retired", (
        "old champion must be RETAINED (retired) in the catalog"
    )
    # Champion-only surfacing tracks the promotion.
    assert "vB:demo" in champ_versions and "vA:demo" not in champ_versions

    # GET /monitoring/models reflects the new champion.
    client = _client()
    models = client.get(
        "/api/v1/monitoring/models", headers=_h(_token(client, "admin@vigil.example"))
    ).json()["items"]
    champ_row = next(
        m for m in models if m["regime"] == regime and m["role"] == "champion"
    )
    assert champ_row["version"] == "vB:demo"

    # Every promotion is audited (model_promote, from→to, non-null actor).
    from sqlalchemy import select

    from vigil.db.models import AuditLog

    with platform_session() as session:
        promos = (
            session.execute(
                select(AuditLog).where(
                    AuditLog.action == "model_promote",
                    AuditLog.detail["regime"].astext == regime,
                )
            )
            .scalars()
            .all()
        )
    assert len(promos) == 2 and all(p.actor_user_id is not None for p in promos)

    # Reversibility: re-promote A → champion again (rollback through the same machinery).
    routing_service.promote(
        admin,
        regime=regime,
        model_version="vA:demo",
        model_card_ref=_CARD,
        eval_provenance="architecture_validation",
    )
    with platform_session() as session:
        champ = routing_repo.get_champion(session, regime=regime)
        reg_a = registry_repo.get_registration(
            session, regime=regime, model_version="vA:demo"
        )
        reg_b = registry_repo.get_registration(
            session, regime=regime, model_version="vB:demo"
        )
    assert champ.model_version == "vA:demo"
    assert reg_a.status == "champion" and reg_b.status == "retired"


# ---------------------------------------------------------------------------
# 4. Register honesty: missing metrics / clinical / register-as-champion / duplicate
# ---------------------------------------------------------------------------


def test_register_honesty_enforced(migrated_db: dict[str, str]) -> None:
    ids = migrated_db
    regime = "m3_honesty"
    admin = _platform_admin_scope(ids["platform_admin"])

    with pytest.raises(RegistrationError, match="metrics"):
        routing_service.register_model(
            admin,
            regime=regime,
            model_version="v_nometrics",
            artifact_ref=_ARTIFACT,
            model_card_ref=_CARD,
            metrics={},
            provenance="architecture_validation",
        )
    with pytest.raises(RegistrationError, match="clinical"):
        routing_service.register_model(
            admin,
            regime=regime,
            model_version="v_clinical",
            artifact_ref=_ARTIFACT,
            model_card_ref=_CARD,
            metrics={"auc": 0.7},
            provenance="clinical_validation",
        )
    with pytest.raises(RegistrationError, match="champion"):
        routing_service.register_model(
            admin,
            regime=regime,
            model_version="v_champ",
            artifact_ref=_ARTIFACT,
            model_card_ref=_CARD,
            metrics={"auc": 0.7},
            provenance="architecture_validation",
            status="champion",
        )

    # First registration of a version succeeds; a duplicate is a hard error.
    routing_service.register_model(
        admin,
        regime=regime,
        model_version="v_dup",
        artifact_ref=_ARTIFACT,
        model_card_ref=_CARD,
        metrics={"auc": 0.7},
        provenance="architecture_validation",
    )
    with pytest.raises(RegistrationError, match="already registered"):
        routing_service.register_model(
            admin,
            regime=regime,
            model_version="v_dup",
            artifact_ref=_ARTIFACT,
            model_card_ref=_CARD,
            metrics={"auc": 0.7},
            provenance="architecture_validation",
        )


# ---------------------------------------------------------------------------
# 5. Registry read is platform/auditor-only; a demonstration registration is labelled
# ---------------------------------------------------------------------------


def test_registry_read_gate_and_demonstration_label(
    migrated_db: dict[str, str],
) -> None:
    ids = migrated_db
    regime = "m3_read"
    admin = _platform_admin_scope(ids["platform_admin"])
    routing_service.register_model(
        admin,
        regime=regime,
        model_version="v_demo:demo",
        artifact_ref=_ARTIFACT,
        model_card_ref=_CARD,
        metrics={"auc": 0.7},
        provenance="demonstration",
        notes="demo registration",
    )

    # Service-level: a tenant scope is denied the read.
    with pytest.raises(RoutingPermissionError):
        routing_service.list_registrations(
            _sponsor_scope(ids["sponsor_a"]), regime=regime
        )

    client = _client()
    # Auditor (platform read) sees it, labelled 'demonstration' with metrics carried.
    r = client.get(
        f"/api/v1/monitoring/registry?regime={regime}",
        headers=_h(_token(client, "auditor@vigil.example")),
    )
    assert r.status_code == 200, r.text
    items = r.json()["items"]
    demo = next(it for it in items if it["model_version"] == "v_demo:demo")
    assert demo["provenance"] == "demonstration", "a demo registration must be labelled"
    assert demo["metrics"] == {"auc": 0.7}

    # Sponsor/site role → 403 on the registry read.
    rd = client.get(
        f"/api/v1/monitoring/registry?regime={regime}",
        headers=_h(_token(client, "coord.a@vigil.example")),
    )
    assert rd.status_code == 403, rd.text
