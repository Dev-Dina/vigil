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

from fastapi import FastAPI
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy import Engine

from guide.config import get_config
from guide.embeddings import get_embedder
from guide.llm import get_guide_llm_client
from guide.observability import create_sink_engine, init_sink
from guide.retrieval import IndexedChunk, load_index
from guide.turn import run_guide_turn

_STATIC_DIR = Path(__file__).resolve().parent / "static"

# (sink_engine, index, embedder, llm) — everything the turn needs, all Guide-owned.
Resources = tuple[Engine, list[IndexedChunk], object, object]


class AskIn(BaseModel):
    question: str = Field(min_length=1, max_length=4000)


class AskOut(BaseModel):
    answer: str
    citations: list[dict]
    guardrail_decision: str  # allowed | blocked
    status: str  # ok | refused


@lru_cache(maxsize=1)
def _default_resources() -> Resources:
    """Build the Guide's own resources once (own sink, own index, own embedder, own LLM client)."""
    cfg = get_config()
    engine = create_sink_engine(cfg.message_events_sink_dsn)
    init_sink(engine)
    index = load_index(cfg.approved_docs_index_path)
    return engine, index, get_embedder(), get_guide_llm_client()


def create_app(resources: Callable[[], Resources] | None = None) -> FastAPI:
    provider = resources or _default_resources
    app = FastAPI(title="Vigil Guide", version="0.3.0")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "guide"}

    @app.get("/")
    async def landing() -> FileResponse:
        # The public chat page — a vanilla static file served BY the Guide; calls only /ask.
        return FileResponse(_STATIC_DIR / "index.html", media_type="text/html")

    @app.post("/ask", response_model=AskOut)
    async def ask(body: AskIn) -> AskOut:
        engine, index, embedder, llm = provider()
        result = run_guide_turn(
            body.question, index=index, embedder=embedder, llm=llm, sink_engine=engine
        )
        return AskOut(
            answer=result.content,
            citations=result.citations,
            guardrail_decision=result.guardrail_decision,
            status=result.status,
        )

    return app


app = create_app()
