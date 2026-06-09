"""End-to-end role-string consistency test (specs/domain.md § Roles).

Asserts that a real backend-emitted JWT carries the canonical role string
"platform_admin" for the ML/platform-admin account, catching any drift between
domain.py, specs, and the frontend PLATFORM_ROLES constant.
"""

from __future__ import annotations

import base64
import json
import os


def _decode_role(token: str) -> str:
    """Decode the `role` claim from a JWT without verifying the signature."""
    payload_b64 = token.split(".")[1]
    # Add padding so base64 decodes cleanly.
    payload_b64 += "=" * (-len(payload_b64) % 4)
    return json.loads(base64.urlsafe_b64decode(payload_b64))["role"]


def test_platform_admin_jwt_role_is_canonical(migrated_db: dict[str, str]) -> None:
    """Backend emits 'platform_admin' for the ML/platform-admin account.

    This test catches end-to-end role-string drift: if domain.py ever changes
    the emitted string, or if the seed uses a different Role enum value, this
    test fails before the mismatch reaches the frontend role gate.
    """
    import asyncio

    from vigil.services.auth_service import login

    pw = os.environ.get("VIGIL_SEED_PASSWORD", "vigil-dev-password")
    result = asyncio.get_event_loop().run_until_complete(
        login("admin@vigil.example", pw)
    )
    token = result.token.access_token
    role = _decode_role(token)
    assert role == "platform_admin", (
        f"Expected JWT role 'platform_admin' for admin@vigil.example, got {role!r}. "
        "Update domain.py or the seed — the canonical string is 'platform_admin' "
        "(specs/domain.md § Roles)."
    )


def test_auditor_jwt_role_is_canonical(migrated_db: dict[str, str]) -> None:
    """Backend emits 'auditor' for the auditor account."""
    import asyncio

    from vigil.services.auth_service import login

    pw = os.environ.get("VIGIL_SEED_PASSWORD", "vigil-dev-password")
    result = asyncio.get_event_loop().run_until_complete(
        login("auditor@vigil.example", pw)
    )
    token = result.token.access_token
    role = _decode_role(token)
    assert role == "auditor", (
        f"Expected JWT role 'auditor' for auditor@vigil.example, got {role!r}."
    )


# Frontend PLATFORM_ROLES constant (duplicated here as the source of truth for the
# cross-layer assertion; update if role-gates.ts changes).
_FRONTEND_PLATFORM_ROLES = {"platform_admin", "auditor"}


def test_emitted_platform_roles_in_frontend_set(migrated_db: dict[str, str]) -> None:
    """Both platform-role JWTs carry strings present in the frontend PLATFORM_ROLES set.

    This is the CI guard that catches the end-to-end mismatch: a backend role
    string not in the frontend set means the role gate silently fails to fire.
    """
    import asyncio

    from vigil.services.auth_service import login

    pw = os.environ.get("VIGIL_SEED_PASSWORD", "vigil-dev-password")

    async def _roles() -> tuple[str, str]:
        a = await login("admin@vigil.example", pw)
        b = await login("auditor@vigil.example", pw)
        return _decode_role(a.token.access_token), _decode_role(b.token.access_token)

    admin_role, auditor_role = asyncio.get_event_loop().run_until_complete(_roles())

    assert admin_role in _FRONTEND_PLATFORM_ROLES, (
        f"admin@vigil.example emits role {admin_role!r}, "
        f"not in frontend PLATFORM_ROLES {_FRONTEND_PLATFORM_ROLES}"
    )
    assert auditor_role in _FRONTEND_PLATFORM_ROLES, (
        f"auditor@vigil.example emits role {auditor_role!r}, "
        f"not in frontend PLATFORM_ROLES {_FRONTEND_PLATFORM_ROLES}"
    )
