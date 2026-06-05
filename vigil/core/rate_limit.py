"""Per-user and per-tenant rate limiting on Redis (infra.md, cheap-path scaling).

Fixed-window counters keyed by (user, minute) and (sponsor, minute). Fail-LOUD on breach
with :class:`RateLimitExceeded`; the router maps it to 429.
"""

from __future__ import annotations

import time

from vigil.core.config import get_settings
from vigil.core.redis import get_redis


class RateLimitExceeded(Exception):
    def __init__(self, scope_label: str) -> None:
        super().__init__(f"rate limit exceeded for {scope_label}")
        self.scope_label = scope_label


async def _hit(key: str, limit: int, label: str) -> None:
    redis = get_redis()
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, 60)
    if count > limit:
        raise RateLimitExceeded(label)


async def enforce(user_id: str, sponsor_id: str | None) -> None:
    """Apply both per-user and per-tenant limits for the current minute window."""
    settings = get_settings()
    window = int(time.time() // 60)
    await _hit(
        f"rl:user:{user_id}:{window}",
        settings.rate_limit_per_user_per_minute,
        f"user {user_id}",
    )
    if sponsor_id is not None:
        await _hit(
            f"rl:tenant:{sponsor_id}:{window}",
            settings.rate_limit_per_tenant_per_minute,
            f"tenant {sponsor_id}",
        )
