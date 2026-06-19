"""Per-user and per-tenant rate limiting on Redis (infra.md, cheap-path scaling).

Fixed-window counters keyed by (user, minute) and (sponsor, minute). Fail-LOUD on breach
with :class:`RateLimitExceeded`; the router maps it to 429.

The same Redis fixed-window primitive backs the **login brute-force throttle** (Gate SEC-RL) for the
unauthenticated `/auth/login` endpoint — which never passes through :func:`enforce` (that runs only
inside ``require_scope``). See :func:`enforce_login`.
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


# --- login brute-force throttle (Gate SEC-RL) --------------------------------------------------
# Same Redis fixed-window primitive, but for the UNAUTHENTICATED /auth/login endpoint. Counts FAILED
# attempts only, keyed PER ACCOUNT (the submitted email) — a single counter that expires
# `login_rate_limit_window_seconds` after the FIRST failure (a fixed lockout window). On exceed the
# router returns 429 + Retry-After (the key's remaining TTL).
#
# Per-IP throttling is DELIBERATELY OMITTED: the deployment is a shared-company environment behind a
# corporate NAT (many users share one egress IP), so a per-IP bucket would let one user's failed
# logins lock out colleagues — a false-positive. The per-account key is the unspoofable defense
# against credential-stuffing and never punishes innocent users sharing an IP.


class LoginRateLimitExceeded(Exception):
    """Too many failed login attempts for an account. Router maps it to 429."""

    def __init__(self, account: str, retry_after: int) -> None:
        super().__init__(f"too many failed login attempts ({account})")
        self.account = account
        self.retry_after = retry_after


def _account_key(account: str) -> str:
    return f"rl:login:acct:{account.lower()}"


async def enforce_login(account: str) -> None:
    """Pre-auth gate: raise if the account is already at/over the failed-attempt threshold.

    The count is NOT incremented here (that happens only on an actual failure) — so this never
    penalises a legitimate caller; it only blocks once the account's failure budget is spent.
    """
    settings = get_settings()
    limit = settings.login_rate_limit_max_failures
    if limit <= 0:  # disabled
        return
    redis = get_redis()
    key = _account_key(account)
    count = int(await redis.get(key) or 0)
    if count >= limit:
        ttl = await redis.ttl(key)
        retry_after = (
            ttl if ttl and ttl > 0 else settings.login_rate_limit_window_seconds
        )
        raise LoginRateLimitExceeded(account, int(retry_after))


async def record_login_failure(account: str) -> None:
    """Count ONE failed attempt against the account (sets the window on the first failure)."""
    settings = get_settings()
    if settings.login_rate_limit_max_failures <= 0:
        return
    redis = get_redis()
    key = _account_key(account)
    count = await redis.incr(key)
    if count == 1:
        await redis.expire(key, settings.login_rate_limit_window_seconds)


async def clear_login_failures(account: str) -> None:
    """Reset the account failure counter after a successful login (legit users never lock out)."""
    await get_redis().delete(_account_key(account))
