"""FastAPI app — modular monolith under /api/v1 (api.md, infra.md).

Middleware assigns a request_id, binds logging context, and renders the uniform error
envelope (ErrorOut). Routers are mounted per domain; each protected route carries the
auth/scope dependency.
"""

from __future__ import annotations

import uuid

from fastapi import FastAPI, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

from vigil.api.routers import (
    admin,
    assistant,
    auth,
    cohort,
    me,
    monitoring,
    participants,
    scoring,
)
from vigil.core.config import get_settings
from vigil.core.logging import configure_logging, get_logger, request_id_var, route_var

log = get_logger("vigil.api")


def create_app() -> FastAPI:
    settings = get_settings()
    configure_logging(settings.log_level)
    app = FastAPI(title="Vigil API", version="0.2.0")

    @app.middleware("http")
    async def context_middleware(request: Request, call_next):  # type: ignore[no-untyped-def]
        request_id = (
            request.headers.get("x-request-id") or f"req_{uuid.uuid4().hex[:12]}"
        )
        token_rid = request_id_var.set(request_id)
        token_route = route_var.set(request.url.path)
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token_rid)
            route_var.reset(token_route)
        response.headers["x-request-id"] = request_id
        return response

    @app.exception_handler(StarletteHTTPException)
    async def http_exc_handler(request: Request, exc: StarletteHTTPException):  # type: ignore[no-untyped-def]
        rid = request_id_var.get() or ""
        return JSONResponse(
            status_code=exc.status_code,
            content={
                "error": _code_for(exc.status_code),
                "detail": str(exc.detail),
                "request_id": rid,
            },
        )

    @app.exception_handler(RequestValidationError)
    async def validation_handler(request: Request, exc: RequestValidationError):  # type: ignore[no-untyped-def]
        rid = request_id_var.get() or ""
        return JSONResponse(
            status_code=422,
            content={
                "error": "validation_error",
                "detail": str(exc.errors()),
                "request_id": rid,
            },
        )

    app.include_router(auth.router, prefix="/api/v1")
    app.include_router(admin.router, prefix="/api/v1")
    app.include_router(cohort.router, prefix="/api/v1")
    app.include_router(participants.router, prefix="/api/v1")
    app.include_router(scoring.router, prefix="/api/v1")
    app.include_router(monitoring.router, prefix="/api/v1")
    app.include_router(assistant.router, prefix="/api/v1")
    app.include_router(me.router, prefix="/api/v1")

    @app.get("/healthz")
    async def healthz() -> dict[str, str]:
        return {"status": "ok"}

    # CORS — added LAST so it is the OUTERMOST middleware: the browser preflight OPTIONS is
    # answered here (200/204 + Access-Control-Allow-* headers) instead of falling through to the
    # routers (which declare no OPTIONS handler → 405). Explicit allow-list (configurable via
    # VIGIL_CORS_ALLOW_ORIGINS), never "*", because allow_credentials=True (the app authenticates
    # with a Bearer token in the Authorization header). Server-side callers send no Origin, so this
    # is inert for them — it only affects browser cross-origin requests.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_allow_origin_list,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["x-request-id"],
    )

    return app


def _code_for(status_code: int) -> str:
    return {
        401: "unauthorized",
        403: "scope_denied",
        404: "not_found",
        429: "rate_limited",
    }.get(status_code, "error")


app = create_app()
