"""Structured JSON logging. Every line carries request_id + scope context; no PII, no secrets."""

from __future__ import annotations

import json
import logging
import sys
from contextvars import ContextVar
from typing import Any

# Request-scoped context, populated by middleware; included on every log line.
request_id_var: ContextVar[str | None] = ContextVar("request_id", default=None)
role_var: ContextVar[str | None] = ContextVar("role", default=None)
route_var: ContextVar[str | None] = ContextVar("route", default=None)


class JsonFormatter(logging.Formatter):
    """Render each record as a single-line JSON object."""

    def format(self, record: logging.LogRecord) -> str:
        payload: dict[str, Any] = {
            "level": record.levelname,
            "logger": record.name,
            "msg": record.getMessage(),
            "request_id": request_id_var.get(),
            "role": role_var.get(),
            "route": route_var.get(),
        }
        if record.exc_info:
            payload["exc"] = self.formatException(record.exc_info)
        # structured extras attached via logger.info(..., extra={"extra": {...}})
        extra = getattr(record, "extra", None)
        if isinstance(extra, dict):
            payload.update(extra)
        return json.dumps(payload, default=str)


def configure_logging(level: str = "INFO") -> None:
    """Install the JSON formatter on the root logger (idempotent)."""
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(JsonFormatter())
    root = logging.getLogger()
    root.handlers = [handler]
    root.setLevel(level.upper())


def get_logger(name: str) -> logging.Logger:
    return logging.getLogger(name)
