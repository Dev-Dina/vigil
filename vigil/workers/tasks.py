"""Task functions. Jobs are idempotent and carry the scope context they need explicitly."""

from __future__ import annotations

from typing import Any

from vigil.core.logging import get_logger

log = get_logger("vigil.worker")


async def ping(ctx: dict[str, Any], note: str = "pong") -> dict[str, str]:
    """Trivial job proving the async path end-to-end (enqueue → worker → result)."""
    log.info("worker.ping", extra={"extra": {"note": note}})
    return {"status": "ok", "note": note}
