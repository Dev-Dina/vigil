"""The Guide FastAPI app (isolated public service, :8080 per specs/infra.md).

7.1 is the isolated SHELL: a health route + a landing stub, no retrieval, no LLM, no guardrails
(those are 7.2/7.3). It imports nothing from `vigil.*` and opens no connection to any real
resource on startup. The structural-isolation proofs (`tests/guide/`) gate this from now on.
"""

from __future__ import annotations

from fastapi import FastAPI


def create_app() -> FastAPI:
    app = FastAPI(title="Vigil Guide", version="0.1.0")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok", "service": "guide"}

    @app.get("/")
    async def landing() -> dict[str, str]:
        return {
            "service": "Vigil Guide",
            "message": (
                "Public project guide. Ask about the Vigil project — answers come from "
                "approved public documents only."
            ),
        }

    return app


app = create_app()
