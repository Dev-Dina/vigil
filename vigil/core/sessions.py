"""Revocable sessions in Redis (infra.md / api.md).

The JWT ``jti`` is the session id and the Redis key. A token is only accepted if its session
is live AND its ``scope_ver`` still matches the stored value — a revoked or stale-scope token
is rejected even before expiry. Logout deletes the key. Refresh tokens are stored alongside.
"""

from __future__ import annotations

import json
import secrets
from dataclasses import asdict, dataclass

from vigil.core.config import get_settings
from vigil.core.redis import get_redis

_SESSION_PREFIX = "session:"
_REFRESH_PREFIX = "refresh:"


@dataclass(frozen=True, slots=True)
class SessionRecord:
    user_id: str
    role: str
    scope_ver: int
    refresh_token: str


def _skey(jti: str) -> str:
    return f"{_SESSION_PREFIX}{jti}"


def _rkey(refresh_token: str) -> str:
    return f"{_REFRESH_PREFIX}{refresh_token}"


async def open_session(jti: str, user_id: str, role: str, scope_ver: int) -> str:
    """Create a live session; return the refresh token (opaque, single-use-ish)."""
    settings = get_settings()
    refresh_token = secrets.token_urlsafe(32)
    record = SessionRecord(
        user_id=user_id, role=role, scope_ver=scope_ver, refresh_token=refresh_token
    )
    redis = get_redis()
    await redis.set(
        _skey(jti), json.dumps(asdict(record)), ex=settings.session_ttl_seconds
    )
    await redis.set(_rkey(refresh_token), jti, ex=settings.refresh_token_ttl_seconds)
    return refresh_token


async def get_session(jti: str) -> SessionRecord | None:
    raw = await get_redis().get(_skey(jti))
    if raw is None:
        return None
    data = json.loads(raw)
    return SessionRecord(**data)


async def is_live(jti: str, scope_ver: int) -> bool:
    """A token is valid only if its session exists AND scope_ver is current."""
    record = await get_session(jti)
    return record is not None and record.scope_ver == scope_ver


async def revoke(jti: str) -> None:
    record = await get_session(jti)
    redis = get_redis()
    await redis.delete(_skey(jti))
    if record is not None:
        await redis.delete(_rkey(record.refresh_token))


async def resolve_refresh(refresh_token: str) -> str | None:
    """Return the live session jti for a refresh token, or None if missing/expired."""
    return await get_redis().get(_rkey(refresh_token))
