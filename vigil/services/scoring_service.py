"""Scoring service — enqueue and poll scoring jobs (specs/scoring.md).

The service validates scope and delegates to the Arq worker; it never runs scoring
synchronously. Scope comes from the verified JWT via the router dependency; the client
never asserts sponsor or trial scope.
"""

from __future__ import annotations

from datetime import datetime
from typing import Literal

from pydantic import BaseModel

from vigil.core.scope import Scope, ScopeError
from vigil.domain import PLATFORM_ROLES, Role
from vigil.workers.settings import enqueue


# ---------------------------------------------------------------------------
# Pydantic schemas (also imported by the router)
# ---------------------------------------------------------------------------


class JobAccepted(BaseModel):
    job_id: str
    trial_id: str
    status: Literal["queued"] = "queued"


class ScoringJobStatus(BaseModel):
    job_id: str
    trial_id: str
    status: Literal["queued", "running", "done", "failed"]
    n_scored: int | None = None
    error: str | None = None
    triggered_at: datetime
    completed_at: datetime | None = None


# ---------------------------------------------------------------------------
# Service functions
# ---------------------------------------------------------------------------


async def trigger_scoring(
    scope: Scope,
    *,
    trial_id: str,
    model_version: str | None = None,
) -> str:
    """Validate scope, enqueue score_trial, return job_id.

    - PLATFORM_ADMIN may trigger (model ops) — no sponsor restriction.
    - AUDITOR is rejected (read-only; router also rejects, belt-and-suspenders).
    - All sponsor/CRO/site roles: trial_id must be within their scope tuples.
    """
    if scope.role == Role.AUDITOR:
        raise ScopeError("auditor may not trigger scoring")

    sponsor_id: str | None = None

    if scope.role in PLATFORM_ROLES:
        # ML admin: no RLS sponsor binding; sponsor_id passed as None to the job.
        # The job must be enqueued with a real sponsor if one is determinable;
        # for platform-triggered scoring, the trial lookup happens inside the job.
        # Here we trust the trial exists and pass no sponsor restriction.
        sponsor_id = None
    else:
        # Scoped user: trial must be within scope.

        # Resolve the sponsor for this trial from the caller's scope.
        # If the caller has exactly one sponsor, use it.  If multiple, the
        # trial_id must belong to one of their tuples.
        matched_sponsor: str | None = None
        for t in scope.tuples:
            if t.trial_id is None or t.trial_id == trial_id:
                matched_sponsor = t.sponsor_id
                break

        if matched_sponsor is None:
            raise ScopeError(
                f"trial {trial_id!r} is not within your scope; scoring denied"
            )
        sponsor_id = matched_sponsor

    job_id = await enqueue(
        "score_trial",
        trial_id,
        model_version,
        sponsor_id=sponsor_id,
    )
    return job_id


async def get_job_status(job_id: str) -> ScoringJobStatus | None:
    """Look up an Arq job by id and map to ScoringJobStatus, or None if the job is not found.

    A Redis/connection failure is an INFRASTRUCTURE outage, NOT "job not found": it raises
    ``ServiceUnavailableError`` (router → 503) so it is never masqueraded as None → 404. A genuinely
    unknown job stays None → 404 (unchanged).
    """
    from arq import create_pool
    from arq.jobs import Job, JobStatus
    from redis.exceptions import RedisError

    from vigil.core.errors import ServiceUnavailableError
    from vigil.workers.settings import _redis_settings

    pool = None
    try:
        pool = await create_pool(_redis_settings())
        job = Job(job_id, pool)
        raw_status = await job.status()

        if raw_status == JobStatus.not_found:
            return None

        result_info = await job.result_info()
        trial_id = ""
        n_scored: int | None = None
        error: str | None = None
        triggered_at = datetime.utcnow()
        completed_at: datetime | None = None

        if result_info is not None:
            triggered_at = result_info.enqueue_time or datetime.utcnow()
            if result_info.success and isinstance(result_info.result, dict):
                res = result_info.result
                trial_id = res.get("trial_id", "")
                n_scored = res.get("n_scored")
                completed_at = result_info.finish_time
            elif not result_info.success:
                error = str(result_info.result)
                completed_at = result_info.finish_time

        # Map arq JobStatus to our enum.
        status_map = {
            JobStatus.queued: "queued",
            JobStatus.deferred: "queued",
            JobStatus.in_progress: "running",
            JobStatus.complete: "done",
            JobStatus.not_found: "failed",
        }
        mapped = status_map.get(raw_status, "failed")

        return ScoringJobStatus(
            job_id=job_id,
            trial_id=trial_id,
            status=mapped,  # type: ignore[arg-type]
            n_scored=n_scored,
            error=error,
            triggered_at=triggered_at,
            completed_at=completed_at,
        )
    except (RedisError, OSError) as exc:
        raise ServiceUnavailableError(
            "scoring job store (redis) is unavailable"
        ) from exc
    finally:
        if pool is not None:
            await pool.aclose()
