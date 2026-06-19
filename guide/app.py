"""The Guide FastAPI app (isolated public service, :8080 per specs/infra.md).

The public guest surface: a static landing/chat page (``GET /``) + ``POST /ask`` (the guardrailed
Guide turn) + ``GET /healthz``. No auth — it's a public surface — but it can reach NOTHING real:
its only tool is approved-doc vector search (isolation proven in `tests/guide/`). Every turn writes
a redacted row to the Guide's OWN sink. Imports nothing from `vigil.*`.

The landing page is a vanilla static file SERVED BY THE GUIDE (`guide/static/index.html`) — it
ships with the Guide, talks ONLY to this service's same-origin ``/ask``, and pulls in NONE of the
real app's frontend / API surface. That is the strongest form of the public-surface isolation.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Callable

from dataclasses import asdict

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import Engine
from sqlalchemy.orm import Session

from guide.cache import ResponseCache
from guide.config import get_config
from guide.embeddings import get_embedder
from guide.llm import get_guide_llm_client
from guide.observability import cost_summary, create_sink_engine, init_sink
from guide.ratelimit import RateLimiter
from guide.retrieval import IndexedChunk, load_index
from guide.turn import run_guide_turn

_STATIC_DIR = Path(__file__).resolve().parent / "static"

# (sink_engine, index, embedder, llm) — everything the turn needs, all Guide-owned.
Resources = tuple[Engine, list[IndexedChunk], object, object]


class AskIn(BaseModel):
    # Cheap abuse guard: cap the input so a giant prompt can't waste tokens (over-length → 422,
    # rejected before retrieval/LLM). 2000 chars is ample for a question about the project.
    question: str = Field(min_length=1, max_length=2000)


class AskOut(BaseModel):
    answer: str
    citations: list[dict]
    guardrail_decision: str  # allowed | blocked
    status: str  # ok | refused


class GuideCostRollupOut(BaseModel):
    llm_provider_model: str
    turns: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    total_cost: float
    total_latency_ms: int
    avg_latency_ms: float


class GuideCostOut(BaseModel):
    """Public-AGGREGATE cost over the Guide's OWN sink — totals + per-model rollups ONLY.

    NEVER a row, NEVER question/answer content, NEVER PII (the Guide holds none). Mirrors the app's
    cost_report shape so the monitoring dashboard can show app + Guide cost as two separate sources.
    """

    surface: str
    total_turns: int
    total_prompt_tokens: int
    total_completion_tokens: int
    total_tokens: int
    total_cost: float
    avg_latency_ms: float
    rollups: list[GuideCostRollupOut]


@lru_cache(maxsize=1)
def _default_resources() -> Resources:
    """Build the Guide's own resources once (own sink, own index, own embedder, own LLM client)."""
    cfg = get_config()
    engine = create_sink_engine(cfg.message_events_sink_dsn)
    init_sink(engine)
    index = load_index(cfg.approved_docs_index_path)
    return engine, index, get_embedder(), get_guide_llm_client()


def create_app(
    resources: Callable[[], Resources] | None = None,
    *,
    rate_limiter: RateLimiter | None = None,
    response_cache: ResponseCache | None = None,
) -> FastAPI:
    cfg = get_config()
    provider = resources or _default_resources
    # Per-app in-memory per-IP throttle (isolation-safe: NO Redis/app coupling; resets on restart).
    # A deploy-layer gateway limit is the durable multi-replica control (see RUNBOOK).
    limiter = rate_limiter or RateLimiter()
    # Per-app in-memory response cache (Gate GUIDE-CACHE) — bounded LRU, isolation-safe (stdlib, NO
    # Redis/app coupling, resets on restart). Built only when enabled; None disables caching.
    cache = response_cache
    if cache is None and cfg.response_cache_enabled:
        cache = ResponseCache(max_size=cfg.response_cache_max_size)
    app = FastAPI(title="Vigil Guide", version="0.3.0")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "guide"}

    @app.get("/")
    async def landing() -> FileResponse:
        # The public chat page — a vanilla static file served BY the Guide; calls only /ask.
        return FileResponse(_STATIC_DIR / "index.html", media_type="text/html")

    @app.post("/ask", response_model=AskOut)
    async def ask(body: AskIn, request: Request) -> AskOut:
        # Per-IP rate limit FIRST — a 429 costs no retrieval or LLM call. Public abuse/cost guard.
        client_ip = request.client.host if request.client else "unknown"
        allowed, retry_after = limiter.check(client_ip)
        if not allowed:
            raise HTTPException(
                status_code=429,
                detail="Rate limit exceeded. Please slow down and try again shortly.",
                headers={"Retry-After": str(retry_after)},
            )
        engine, index, embedder, llm = provider()
        result = run_guide_turn(
            body.question,
            index=index,
            embedder=embedder,
            llm=llm,
            sink_engine=engine,
            cache=cache,
        )
        return AskOut(
            answer=result.content,
            citations=result.citations,
            guardrail_decision=result.guardrail_decision,
            status=result.status,
        )

    @app.get("/metrics/cost", response_model=GuideCostOut)
    async def metrics_cost() -> GuideCostOut:
        # Read-only PUBLIC-AGGREGATE of the Guide's OWN sink (Gate GUIDE-COST): totals + per-model
        # rollups only — never rows, never question/answer content, never PII (the Guide has none).
        # Reads ONLY the Guide's own store; touches NO app DB / Redis / Vault. Honest-zero on an
        # empty sink. Lets the monitoring dashboard show app + Guide cost as two separate sources.
        engine, _index, _embedder, _llm = provider()
        with Session(engine) as session:
            summary = cost_summary(session)
        return GuideCostOut(**asdict(summary))

    return app


app = create_app()
