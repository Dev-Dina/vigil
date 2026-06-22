"""Auth/scope dependency — injected into every protected route (api.md).

``require_scope`` verifies the bearer token (signature, live Redis session, current
scope_ver) and returns the immutable :class:`Scope`. No handler reaches a repository without
a resolved scope; the client never asserts scope. It also enforces per-user/per-tenant rate
limits on every protected call.
"""

from __future__ import annotations

from fastapi import Depends, Header, HTTPException, status

from vigil.core import rate_limit
from vigil.core.scope import Scope
from vigil.core.security import TokenError, decode_token
from vigil.services.auth_service import AuthError, authenticate_token


def _bearer(authorization: str) -> str:
    if not authorization.lower().startswith("bearer "):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail="missing bearer token"
        )
    return authorization.split(" ", 1)[1].strip()


async def require_jti(authorization: str = Header(default="")) -> str:
    """Return the session id from a verified token (for logout). Signature is checked."""
    try:
        claims = decode_token(_bearer(authorization))
    except TokenError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
        ) from exc
    return claims["jti"]


async def require_scope(authorization: str = Header(default="")) -> Scope:
    token = _bearer(authorization)
    try:
        scope = await authenticate_token(token)
    except (AuthError, TokenError) as exc:
        # AuthError = dead session / stale scope; TokenError = bad signature / expired / malformed
        # (decode_token). Both are auth failures → 401 (never an unhandled 500); mirrors require_jti.
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
        ) from exc

    sponsor = next(iter(scope.sponsor_ids), None)
    try:
        await rate_limit.enforce(scope.user_id, sponsor)
    except rate_limit.RateLimitExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS, detail=str(exc)
        ) from exc
    return scope


ScopeDep = Depends(require_scope)
