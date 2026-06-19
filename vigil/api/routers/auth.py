"""auth router (api.md /auth) — unauthenticated entry + session lifecycle."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, EmailStr, SecretStr

from vigil.api.deps import ScopeDep, require_jti
from vigil.core import rate_limit
from vigil.core.scope import Scope, ScopeTuple
from vigil.domain import Role
from vigil.services import auth_service
from vigil.services.auth_service import AuthError

router = APIRouter(prefix="/auth", tags=["auth"])


class LoginIn(BaseModel):
    email: EmailStr
    password: SecretStr


class RefreshIn(BaseModel):
    refresh_token: SecretStr


class TokenOut(BaseModel):
    access_token: str
    token_type: Literal["bearer"] = "bearer"
    expires_in: int
    refresh_token: str


class ScopeTupleOut(BaseModel):
    sponsor_id: str
    trial_id: str | None
    site_id: str | None


class MeOut(BaseModel):
    user_id: str
    role: Role
    home_sponsor_id: str | None
    home_cro_id: str | None
    scope: list[ScopeTupleOut]


def _token_out(result: auth_service.LoginResult) -> TokenOut:
    return TokenOut(
        access_token=result.token.access_token,
        expires_in=result.token.expires_in,
        refresh_token=result.refresh_token,
    )


@router.post("/login", response_model=TokenOut)
async def login(body: LoginIn) -> TokenOut:
    email = str(body.email)
    # Pre-auth gate: an account over its failed-attempt budget is rejected WITHOUT a credential
    # check (so a locked account can't be probed) — credential-stuffing is throttled, not creds.
    try:
        await rate_limit.enforce_login(email)
    except rate_limit.LoginRateLimitExceeded as exc:
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=str(exc),
            headers={"Retry-After": str(exc.retry_after)},
        ) from exc
    try:
        result = await auth_service.login(email, body.password.get_secret_value())
    except AuthError as exc:
        await rate_limit.record_login_failure(email)  # count this failed attempt
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
        ) from exc
    await rate_limit.clear_login_failures(email)  # success → reset the account counter
    return _token_out(result)


# NOTE: /auth/refresh is intentionally NOT throttled. The throttle keys on the account (email), and
# a refresh request carries no account identity — only the opaque, high-entropy refresh token, which
# is infeasible to brute-force. Per-IP throttling was rejected (shared corporate NAT → false
# positives), so there is no IP fallback here either.
@router.post("/refresh", response_model=TokenOut)
async def refresh(body: RefreshIn) -> TokenOut:
    try:
        result = await auth_service.refresh(body.refresh_token.get_secret_value())
    except AuthError as exc:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED, detail=str(exc)
        ) from exc
    return _token_out(result)


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(jti: str = Depends(require_jti)) -> Response:
    await auth_service.logout(jti)
    return Response(status_code=status.HTTP_204_NO_CONTENT)


def _scope_out(scope: Scope) -> list[ScopeTupleOut]:
    return [
        ScopeTupleOut(sponsor_id=t.sponsor_id, trial_id=t.trial_id, site_id=t.site_id)
        for t in scope.tuples
    ]


@router.get("/me", response_model=MeOut)
async def me(scope: Scope = ScopeDep) -> MeOut:
    return MeOut(
        user_id=scope.user_id,
        role=scope.role,
        home_sponsor_id=scope.home_sponsor_id,
        home_cro_id=scope.home_cro_id,
        scope=_scope_out(scope),
    )


__all__ = ["router", "Scope", "ScopeTuple"]
