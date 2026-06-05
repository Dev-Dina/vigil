"""admin router (api.md /admin) — scoped administration; every write subset-checked + audited."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel, EmailStr

from vigil.api.deps import ScopeDep
from vigil.core.scope import Scope, ScopeError
from vigil.domain import Role
from vigil.services import admin_service
from vigil.services.admin_service import AdminError, GrantSpec

router = APIRouter(prefix="/admin", tags=["admin"])


class GrantIn(BaseModel):
    sponsor_id: str
    trial_id: str | None = None
    site_id: str | None = None


class UserCreateIn(BaseModel):
    email: EmailStr
    role: Role
    home_sponsor_id: str | None = None
    home_cro_id: str | None = None
    initial_grants: list[GrantIn] = []


class UserOut(BaseModel):
    user_id: str
    email: EmailStr
    role: Role
    home_sponsor_id: str | None
    home_cro_id: str | None
    created_at: datetime


class GrantOut(GrantIn):
    grant_id: str
    granted_by: str
    granted_at: datetime


class SponsorCreateIn(BaseModel):
    name: str


class SponsorOut(BaseModel):
    sponsor_id: str
    name: str


def _map_errors(exc: Exception) -> HTTPException:
    if isinstance(exc, ScopeError):
        return HTTPException(status_code=status.HTTP_403_FORBIDDEN, detail=str(exc))
    if isinstance(exc, AdminError):
        return HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc))
    raise exc


@router.post("/users", response_model=UserOut, status_code=status.HTTP_201_CREATED)
async def create_user(body: UserCreateIn, scope: Scope = ScopeDep) -> UserOut:
    try:
        created = admin_service.create_user(
            scope,
            email=str(body.email),
            role=body.role,
            home_sponsor_id=body.home_sponsor_id,
            home_cro_id=body.home_cro_id,
            initial_grants=[
                GrantSpec(g.sponsor_id, g.trial_id, g.site_id)
                for g in body.initial_grants
            ],
        )
    except (ScopeError, AdminError) as exc:
        raise _map_errors(exc) from exc
    return UserOut(
        user_id=created.user_id,
        email=created.email,
        role=Role(created.role),
        home_sponsor_id=created.home_sponsor_id,
        home_cro_id=created.home_cro_id,
        created_at=datetime.now(UTC),
    )


@router.post(
    "/users/{user_id}/grants",
    response_model=GrantOut,
    status_code=status.HTTP_201_CREATED,
)
async def add_grant(user_id: str, body: GrantIn, scope: Scope = ScopeDep) -> GrantOut:
    try:
        created = admin_service.add_grant(
            scope,
            target_user_id=user_id,
            grant=GrantSpec(body.sponsor_id, body.trial_id, body.site_id),
        )
    except (ScopeError, AdminError) as exc:
        raise _map_errors(exc) from exc
    return GrantOut(
        grant_id=created.grant_id,
        sponsor_id=created.sponsor_id,
        trial_id=created.trial_id,
        site_id=created.site_id,
        granted_by=created.granted_by,
        granted_at=datetime.now(UTC),
    )


@router.delete(
    "/users/{user_id}/grants/{grant_id}", status_code=status.HTTP_204_NO_CONTENT
)
async def revoke_grant(user_id: str, grant_id: str, scope: Scope = ScopeDep) -> None:
    try:
        admin_service.revoke_grant(scope, target_user_id=user_id, grant_id=grant_id)
    except (ScopeError, AdminError) as exc:
        raise _map_errors(exc) from exc


@router.post(
    "/sponsors", response_model=SponsorOut, status_code=status.HTTP_201_CREATED
)
async def create_sponsor(body: SponsorCreateIn, scope: Scope = ScopeDep) -> SponsorOut:
    try:
        sponsor_id, name = admin_service.create_sponsor(scope, name=body.name)
    except (ScopeError, AdminError) as exc:
        raise _map_errors(exc) from exc
    return SponsorOut(sponsor_id=sponsor_id, name=name)
