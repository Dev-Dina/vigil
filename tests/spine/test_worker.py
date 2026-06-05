"""Async-path smoke test: enqueue a job, run the worker, assert it executed (infra.md)."""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text  # noqa: F401 - keeps env import ordering consistent

os.environ.setdefault("VIGIL_SECRETS_BACKEND", "env")
os.environ.setdefault("VIGIL_JWT_SIGNING_KEY", "test-signing-key-not-a-secret")
os.environ.setdefault("VIGIL_LLM_API_KEY", "test")
os.environ.setdefault("VIGIL_DB_DSN", "postgresql+psycopg://x:y@localhost:1/none")
os.environ.setdefault("VIGIL_REDIS_URL", "redis://localhost:6379/15")


def _redis_reachable() -> bool:
    import redis as sync_redis

    try:
        client = sync_redis.from_url(os.environ["VIGIL_REDIS_URL"])
        client.ping()
        return True
    except Exception:  # noqa: BLE001
        return False


@pytest.mark.asyncio
async def test_enqueue_then_run_ping():
    pytest.importorskip("arq")
    if not _redis_reachable():
        pytest.skip("no Redis reachable for the queue smoke test")

    from arq.worker import Worker

    from vigil.workers.settings import WorkerSettings, enqueue

    job_id = await enqueue("ping", note="async-path")
    assert job_id

    worker = Worker(
        functions=WorkerSettings.functions,
        redis_settings=WorkerSettings.redis_settings,
        burst=True,  # process the queued job then stop
        poll_delay=0.1,
    )
    await worker.main()
    assert worker.jobs_complete >= 1
    await worker.close()
