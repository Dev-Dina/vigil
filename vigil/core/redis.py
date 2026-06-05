"""Redis client factory (sessions, cache, rate limit) — one URL from config.

Async Redis clients are bound to the event loop that first used their connection pool. To
stay correct under any loop (ASGI server, TestClient's per-request loops, Arq's loop), we
keep ONE connection pool per event loop and hand out a client bound to it. This avoids the
"future attached to a different loop" error while still pooling connections within a loop.
"""

from __future__ import annotations

import asyncio
from weakref import WeakKeyDictionary

import redis.asyncio as aioredis

from vigil.core.config import get_settings

_pools: WeakKeyDictionary[asyncio.AbstractEventLoop, aioredis.ConnectionPool] = (
    WeakKeyDictionary()
)


def get_redis() -> aioredis.Redis:
    """Return an async Redis client bound to the current event loop's connection pool."""
    loop = asyncio.get_event_loop()
    pool = _pools.get(loop)
    if pool is None:
        pool = aioredis.ConnectionPool.from_url(
            get_settings().redis_url, encoding="utf-8", decode_responses=True
        )
        _pools[loop] = pool
    return aioredis.Redis(connection_pool=pool)
