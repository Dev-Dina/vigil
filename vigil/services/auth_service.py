"""Auth domain logic: login, refresh, logout, identity echo.

Orchestrates the auth-lookup session (pre-scope read of the authenticating user), scope
resolution, token minting, and the Redis session lifecycle. Returns plain values to the
router; no HTTP concerns here.
"""

from __future__ import annotations

from dataclasses import dataclass

from vigil.core import sessions
from vigil.core.scope import Scope
from vigil.core.security import (
    MintedToken,
    decode_token,
    mint_access_token,
    scope_from_claims,
    verify_password,
)
from vigil.repositories import users as user_repo
from vigil.repositories.session import auth_lookup_session
from vigil.services.scope_resolver import resolve_scope


class AuthError(Exception):
    """Bad credentials / dead session / stale scope. Surfaces as 401."""


@dataclass(frozen=True, slots=True)
class LoginResult:
    token: MintedToken
    refresh_token: str
    scope: Scope


def _resolve_login_scope(email: str, password: str) -> Scope:
    with auth_lookup_session() as session:
        user = user_repo.get_by_email(session, email)
        if user is None or not user.is_active:
            raise AuthError("invalid credentials")
        if not verify_password(password, user.password_hash):
            raise AuthError("invalid credentials")
        return resolve_scope(session, user)


async def login(email: str, password: str) -> LoginResult:
    scope = _resolve_login_scope(email, password)
    token = mint_access_token(scope)
    refresh_token = await sessions.open_session(
        token.jti, scope.user_id, str(scope.role), scope.scope_ver
    )
    return LoginResult(token=token, refresh_token=refresh_token, scope=scope)


async def refresh(refresh_token: str) -> LoginResult:
    jti = await sessions.resolve_refresh(refresh_token)
    if jti is None:
        raise AuthError("refresh token invalid or expired")
    record = await sessions.get_session(jti)
    if record is None:
        raise AuthError("session revoked")
    # Re-resolve scope from the DB so a scope_ver bump invalidates the old token.
    with auth_lookup_session() as session:
        import uuid

        user = user_repo.get_by_id(session, uuid.UUID(record.user_id))
        if user is None or not user.is_active:
            raise AuthError("user inactive")
        scope = resolve_scope(session, user)
    if scope.scope_ver != record.scope_ver:
        # Stored session is stale; force a fresh login.
        await sessions.revoke(jti)
        raise AuthError("scope changed; re-login required")
    # Rotate: revoke old session, open a new one.
    await sessions.revoke(jti)
    token = mint_access_token(scope)
    new_refresh = await sessions.open_session(
        token.jti, scope.user_id, str(scope.role), scope.scope_ver
    )
    return LoginResult(token=token, refresh_token=new_refresh, scope=scope)


async def logout(jti: str) -> None:
    await sessions.revoke(jti)


async def authenticate_token(token: str) -> Scope:
    """Verify a bearer token end-to-end: signature, live session, current scope_ver."""
    claims = decode_token(token)
    scope = scope_from_claims(claims)
    if not await sessions.is_live(claims["jti"], scope.scope_ver):
        raise AuthError("session revoked or scope stale")
    return scope
