"""Arq worker config + the enqueue helper.

Bounded concurrency + jittered exponential backoff (CLAUDE.md resilience). The Redis pool
URL comes from one config object. Routers enqueue via :func:`enqueue`; they never run the
work inline.
"""

from __future__ import annotations

from typing import Any

from arq import create_pool
from arq.connections import RedisSettings

from vigil.core.config import get_settings
from vigil.workers.tasks import ping, run_assistant_turn, score_trial


def _redis_settings() -> RedisSettings:
    return RedisSettings.from_dsn(get_settings().redis_url)


class WorkerSettings:
    """Consumed by ``arq vigil.workers.settings.WorkerSettings``."""

    functions = [ping, score_trial, run_assistant_turn]
    redis_settings = _redis_settings()
    max_jobs = 10  # bounded concurrency / backpressure
    max_tries = 5
    retry_jobs = True
    job_timeout = 300


async def enqueue(function: str, *args: Any, **kwargs: Any) -> str:
    """Enqueue a job; return its id. The single sanctioned path off the request thread."""
    pool = await create_pool(_redis_settings())
    try:
        job = await pool.enqueue_job(function, *args, **kwargs)
        if job is None:
            raise RuntimeError(f"failed to enqueue {function}")
        return job.job_id
    finally:
        await pool.aclose()
