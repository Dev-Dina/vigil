"""In-memory bounded LRU response cache for the isolated Guide (Gate GUIDE-CACHE).

Isolation-safe BY CONSTRUCTION: a process-local, bounded LRU (stdlib ``OrderedDict`` + a lock) —
NOT Redis, NOT semantic, NOT a shared store. Same isolation reasoning as the in-memory rate limit
(``guide.ratelimit``): the Guide must NOT couple to the app's Redis/infra. It caches ONLY allowed,
SUCCESSFUL Guide answers and is consulted ONLY on the allowed path, AFTER the keyword + topical
guards — so a cache entry can NEVER bypass a guard. Per-process (multi-worker → independent
per-worker caches, which is fine) and resets on restart (staleness bounded by process lifetime).
Exact-match after normalization (lowercase / strip / collapse whitespace), never fuzzy/semantic.
Imports nothing from ``vigil.*``; no provider SDK.
"""

from __future__ import annotations

import hashlib
import re
import threading
from collections import OrderedDict
from dataclasses import dataclass

_WS = re.compile(r"\s+")


def normalize_key(question: str) -> str:
    """Stable key for an EXACT-match (post-normalization) lookup — NOT semantic.

    Lowercase, strip, collapse internal whitespace, then a stable SHA-256 hex digest so
    ``"What is Vigil?"`` and ``"  what is vigil?  "`` map to the same entry.
    """
    norm = _WS.sub(" ", question.strip().lower())
    return hashlib.sha256(norm.encode("utf-8")).hexdigest()


@dataclass(frozen=True, slots=True)
class CachedAnswer:
    """The cached payload — enough to reproduce the SAME allowed/ok response (answer + citations)."""

    content: str
    citations: list[dict]


class ResponseCache:
    """A bounded, thread-safe LRU of ``normalize_key(question) -> CachedAnswer`` (newest at the end)."""

    def __init__(self, *, max_size: int = 256) -> None:
        self._max = max(1, max_size)
        self._lock = threading.Lock()
        self._store: OrderedDict[str, CachedAnswer] = OrderedDict()

    def get(self, key: str) -> CachedAnswer | None:
        """Return the cached payload for ``key`` (and mark it most-recently-used), or ``None``."""
        with self._lock:
            payload = self._store.get(key)
            if payload is None:
                return None
            self._store.move_to_end(key)  # LRU touch
            return payload

    def set(self, key: str, payload: CachedAnswer) -> None:
        """Store ``payload`` under ``key`` (most-recent), evicting the oldest past ``max_size``."""
        with self._lock:
            self._store[key] = payload
            self._store.move_to_end(key)
            while len(self._store) > self._max:
                self._store.popitem(last=False)  # evict the least-recently-used

    def __len__(self) -> int:
        with self._lock:
            return len(self._store)
