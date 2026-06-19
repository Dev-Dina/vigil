"""Gate RBAC-OPS — operational platform-tier roles (mlops_engineer + llmops_engineer).

Proves the ENFORCED least-privilege boundary on top of the seven data-scope roles:

- ACTION boundary (the meaningful claim): the model-lifecycle WRITE actions (drift-run, register,
  promote) allow platform_admin + mlops_engineer and DENY llmops_engineer (403) AND auditor (403).
  An LLMOps engineer is authorizationally barred from touching the model lifecycle.
- READ boundary: each ops role reads its OWN surfaces only — mlops_engineer reads drift/registry/
  model-traffic (403 on cost/messages); llmops_engineer reads cost/messages (403 on drift/registry/
  models). platform_admin + auditor read EVERYTHING (the superset / read-all).
- Identity: the two seeded ops users log in and /auth/me returns the canonical role + no sponsor scope.

The sacred re-proof (platform-tier, NO sponsor scope, NO tenant-row reach for the new roles) lives in
test_leakage.py. Real Postgres + Redis via migrated_db (RLS + sessions are not mockable); the drift-run
ACTION gate is exercised hermetically at the service layer (enqueue monkeypatched — no arq/worker).
"""

from __future__ import annotations

import asyncio
import os
import uuid

import pytest
from fastapi.testclient import TestClient

from vigil.api.app import create_app
from vigil.core.scope import Scope
from vigil.domain import Role
from vigil.services import monitoring_service
from vigil.services.monitoring_service import MonitoringPermissionError

_CARD = "data/models/t2d/model_card.md"
_ARTIFACT = "data/models/t2d/seq_v2_demo.pt"


def _client() -> TestClient:
    return TestClient(create_app(), raise_server_exceptions=False)


def _token(client: TestClient, email: str) -> str:
    pw = os.environ.get("VIGIL_SEED_PASSWORD", "vigil-dev-password")
    r = client.post("/api/v1/auth/login", json={"email": email, "password": pw})
    assert r.status_code == 200, r.text
    return r.json()["access_token"]


def _h(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _ops_scope(user_id: str, role: Role) -> Scope:
    """A platform-tier scope for an operational role — NO sponsor scope (empty tuples)."""
    return Scope(
        user_id=user_id,
        role=role,
        home_sponsor_id=None,
        home_cro_id=None,
        tuples=(),
        scope_ver=1,
    )


def _seed_champion(regime: str, version: str) -> None:
    """Seed a champion routing_state row so a promote has a row to update (mirrors test_m3)."""
    from vigil.db.models import RoutingState
    from vigil.repositories.session import platform_session

    with platform_session() as session:
        session.add(
            RoutingState(
                id=uuid.uuid4(),
                regime=regime,
                role="champion",
                model_version=version,
                model_card_ref=_CARD,
                health="healthy",
            )
        )
        session.flush()


# ---------------------------------------------------------------------------
# 1. ACTION boundary — register + promote (HTTP): mlops ALLOWED, llmops + auditor DENIED
# ---------------------------------------------------------------------------


def test_register_and_promote_action_boundary(migrated_db: dict[str, str]) -> None:
    regime = f"rbac_action_{uuid.uuid4().hex[:8]}"
    _seed_champion(
        regime, "v_rbac_start"
    )  # a champion to update on promote (test_m3 pattern)
    client = _client()
    mlops = _h(_token(client, "mlops@vigil.example"))
    llmops = _h(_token(client, "llmops@vigil.example"))
    auditor = _h(_token(client, "auditor@vigil.example"))

    reg_body = {
        "regime": regime,
        "model_version": "v_rbac",
        "artifact_ref": _ARTIFACT,
        "model_card_ref": _CARD,
        "metrics": {"auc": 0.77},
        "provenance": "architecture_validation",
    }
    promo_body = {
        "regime": regime,
        "model_version": "v_rbac",
        "model_card_ref": _CARD,
        "eval_provenance": "architecture_validation",
    }

    # llmops_engineer is BARRED from the model lifecycle (the least-privilege point).
    assert (
        client.post(
            "/api/v1/monitoring/models/register", json=reg_body, headers=llmops
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/monitoring/models/promote", json=promo_body, headers=llmops
        ).status_code
        == 403
    )
    # auditor is read-only — denied the write actions (unchanged behaviour).
    assert (
        client.post(
            "/api/v1/monitoring/models/register", json=reg_body, headers=auditor
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/api/v1/monitoring/models/promote", json=promo_body, headers=auditor
        ).status_code
        == 403
    )

    # mlops_engineer OWNS the model lifecycle: register (201) then promote (201).
    r = client.post("/api/v1/monitoring/models/register", json=reg_body, headers=mlops)
    assert r.status_code == 201, r.text
    assert r.json()["status"] == "challenger"
    r = client.post("/api/v1/monitoring/models/promote", json=promo_body, headers=mlops)
    assert r.status_code == 201, r.text
    assert r.json()["to_version"] == "v_rbac"


# ---------------------------------------------------------------------------
# 2. ACTION boundary — drift-run (service-level, hermetic): mlops ALLOWED, llmops + auditor DENIED
# ---------------------------------------------------------------------------


def test_drift_run_action_boundary(migrated_db: dict[str, str], monkeypatch) -> None:  # type: ignore[no-untyped-def]
    ids = migrated_db

    async def _fake_enqueue(task: str, **kwargs):  # type: ignore[no-untyped-def]
        return f"job-{task}"

    monkeypatch.setattr("vigil.workers.settings.enqueue", _fake_enqueue)
    loop = asyncio.get_event_loop()

    mlops = _ops_scope(ids["mlops_engineer"], Role.MLOPS_ENGINEER)
    llmops = _ops_scope(ids["llmops_engineer"], Role.LLMOPS_ENGINEER)
    auditor = _ops_scope(ids["auditor"], Role.AUDITOR)

    # mlops_engineer + platform_admin may trigger; the gate passes BEFORE enqueue.
    job = loop.run_until_complete(
        monitoring_service.trigger_drift_run(mlops, regime="t2d", demo_shift=0.0)
    )
    assert job == "job-compute_drift"

    # llmops_engineer + auditor are denied — triggering a job is a model-lifecycle action.
    for scope in (llmops, auditor):
        with pytest.raises(MonitoringPermissionError):
            loop.run_until_complete(
                monitoring_service.trigger_drift_run(scope, regime="t2d")
            )


# ---------------------------------------------------------------------------
# 3. READ boundary — each ops role reads ONLY its own surfaces
# ---------------------------------------------------------------------------

_MLOPS_READS = (
    "/api/v1/monitoring/drift",
    "/api/v1/monitoring/registry",
    "/api/v1/monitoring/models",
)
_LLMOPS_READS = (
    "/api/v1/monitoring/cost",
    "/api/v1/monitoring/messages",
)


def test_mlops_reads_own_surfaces_only(migrated_db: dict[str, str]) -> None:
    client = _client()
    h = _h(_token(client, "mlops@vigil.example"))
    for path in _MLOPS_READS:
        assert client.get(path, headers=h).status_code == 200, (
            f"mlops denied own read {path}"
        )
    for path in _LLMOPS_READS:
        assert client.get(path, headers=h).status_code == 403, (
            f"mlops reached LLMOps read {path}"
        )


def test_llmops_reads_own_surfaces_only(migrated_db: dict[str, str]) -> None:
    client = _client()
    h = _h(_token(client, "llmops@vigil.example"))
    for path in _LLMOPS_READS:
        assert client.get(path, headers=h).status_code == 200, (
            f"llmops denied own read {path}"
        )
    for path in _MLOPS_READS:
        assert client.get(path, headers=h).status_code == 403, (
            f"llmops reached MLOps read {path}"
        )


def test_platform_admin_and_auditor_read_all(migrated_db: dict[str, str]) -> None:
    client = _client()
    for email in ("admin@vigil.example", "auditor@vigil.example"):
        h = _h(_token(client, email))
        for path in _MLOPS_READS + _LLMOPS_READS:
            assert client.get(path, headers=h).status_code == 200, (
                f"{email} denied {path}"
            )


# ---------------------------------------------------------------------------
# 4. Identity — the seeded ops users log in and /auth/me returns the canonical role, no sponsor
# ---------------------------------------------------------------------------


@pytest.mark.parametrize(
    "email,role_str",
    [
        ("mlops@vigil.example", "mlops_engineer"),
        ("llmops@vigil.example", "llmops_engineer"),
    ],
)
def test_ops_user_me_role_and_no_sponsor(
    migrated_db: dict[str, str], email: str, role_str: str
) -> None:
    client = _client()
    me = client.get("/api/v1/auth/me", headers=_h(_token(client, email))).json()
    assert me["role"] == role_str
    assert me["home_sponsor_id"] is None and me["home_cro_id"] is None
    assert me["scope"] == []
