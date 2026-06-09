"""Routing B1 tests — routing_state table and champion resolver.

Covers specs/routing.md § Regime routing (resolver contract) and
§ Leakage / isolation invariants (iii).

Requires a live Postgres via the migrated_db fixture (RLS and routing_state
are not mockable). Tests skip automatically when no Postgres is reachable.
"""

from __future__ import annotations

import pytest

from vigil.core.scope import Scope, ScopeTuple
from vigil.domain import Role
from vigil.services.routing_service import RoutingPermissionError
from vigil.services.routing_service import get_champion as svc_get_champion
from vigil.workers.tasks import _DEMO_MODEL_VERSION, _resolve_champion_version


def _platform_scope() -> Scope:
    return Scope(
        user_id="00000000-0000-0000-0000-000000000001",
        role=Role.PLATFORM_ADMIN,
        home_sponsor_id=None,
        home_cro_id=None,
        tuples=(),
        scope_ver=1,
    )


# ---------------------------------------------------------------------------
# Resolver: champion present → model_version returned
# ---------------------------------------------------------------------------


def test_resolver_returns_champion_version(migrated_db: dict[str, str]) -> None:
    """Champion row seeded for 't2d' → resolver returns its model_version."""
    version = _resolve_champion_version("t2d")
    assert version == _DEMO_MODEL_VERSION, (
        f"Expected resolver to return {_DEMO_MODEL_VERSION!r} via routing_state, "
        f"got {version!r}"
    )


# ---------------------------------------------------------------------------
# Resolver: no champion → hard error, not a sentinel
# ---------------------------------------------------------------------------


def test_resolver_raises_when_no_champion(migrated_db: dict[str, str]) -> None:
    """No champion row for unknown regime → ValueError; sentinel is NOT returned."""
    with pytest.raises(ValueError, match="No champion model registered for regime"):
        _resolve_champion_version("regime_that_does_not_exist_b1_test")


# ---------------------------------------------------------------------------
# Invariant (iii): platform reads routing_state; sponsor blocked at app layer
# ---------------------------------------------------------------------------


def test_routing_state_platform_access_sponsor_blocked(
    migrated_db: dict[str, str],
) -> None:
    """specs/routing.md invariant (iii): platform_admin reads routing_state;
    sponsor-role is blocked at the app layer (not by RLS — table has no RLS).

    Asserts both directions in one test.
    """
    # Direction 1: platform_admin scope can read routing_state.
    platform_scope = _platform_scope()
    row = svc_get_champion(platform_scope, regime="t2d")
    assert row.model_version == _DEMO_MODEL_VERSION
    assert row.regime == "t2d"
    assert row.role == "champion"
    assert row.health == "healthy"

    # Direction 2: sponsor-scoped role is blocked at the app layer (not RLS).
    # routing_state has no RLS policy; a sponsor session would see the rows at the DB
    # level if it reached the repo — the block is purely the scope check here.
    sponsor_scope = Scope(
        user_id="00000000-0000-0000-0000-000000000002",
        role=Role.COORDINATOR,
        home_sponsor_id=migrated_db["sponsor_a"],
        home_cro_id=None,
        tuples=(ScopeTuple(sponsor_id=migrated_db["sponsor_a"]),),
        scope_ver=1,
    )
    with pytest.raises(RoutingPermissionError, match="not platform-scoped"):
        svc_get_champion(sponsor_scope, regime="t2d")


# ---------------------------------------------------------------------------
# No-regression: demo regime resolves to the demo version via routing_state
# ---------------------------------------------------------------------------


def test_demo_regime_no_regression(migrated_db: dict[str, str]) -> None:
    """t2d champion resolves to sequence_v1.0:demo via routing_state (no sentinel path).

    Proves the seed row is present and the resolver uses it, not the old sentinel.
    """
    assert _resolve_champion_version("t2d") == "sequence_v1.0:demo"
    assert migrated_db["routing_t2d_champion"] == "sequence_v1.0:demo"
