"""In-memory per-IP fixed-window rate limiter for the Guide's public /ask (isolation-safe).

The Guide is a SEPARATE, isolated service: it must NOT depend on the app's Redis (that would
couple it to app infra and break the isolation invariant). So this is a process-local fixed-window
counter — stdlib only (``time`` + ``threading``), resets on restart. It is a cheap first line
against burst abuse / token-waste on the public endpoint; a deploy-layer gateway/proxy limit is the
durable, multi-replica control (see RUNBOOK). Imports nothing from ``vigil.*``.
"""

from __future__ import annotations

import threading
import time

_DEFAULT_LIMIT = 30  # requests per IP per window
_DEFAULT_WINDOW = 60  # seconds


class RateLimiter:
    """Process-local fixed-window per-IP limiter. ``check(ip)`` counts the request when allowed."""

    def __init__(
        self, *, limit: int = _DEFAULT_LIMIT, window_seconds: int = _DEFAULT_WINDOW
    ) -> None:
        self._limit = limit
        self._window = window_seconds
        self._lock = threading.Lock()
        self._hits: dict[tuple[str, int], int] = {}

    def check(self, ip: str, *, now: float | None = None) -> tuple[bool, int]:
        """Return ``(allowed, retry_after_seconds)``; counts this request when allowed.

        Fixed window: requests are bucketed by ``floor(now / window)``. On exceeding ``limit`` in
        the current bucket, returns ``(False, seconds_until_next_window)`` without counting.
        """
        t = time.time() if now is None else now
        bucket = int(t // self._window)
        with self._lock:
            # Prune stale windows (keep only the current bucket) so the map can't grow unbounded.
            self._hits = {k: v for k, v in self._hits.items() if k[1] == bucket}
            key = (ip, bucket)
            count = self._hits.get(key, 0)
            if count >= self._limit:
                retry = self._window - int(t % self._window)
                return False, max(1, retry)
            self._hits[key] = count + 1
            return True, 0
