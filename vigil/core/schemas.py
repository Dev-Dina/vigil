"""Shared response/pagination envelopes (api.md)."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel


class Page(BaseModel):
    items: list[Any]
    next_cursor: str | None = None
    total: int


class ErrorOut(BaseModel):
    error: str  # machine code, e.g. "scope_denied"
    detail: str
    request_id: str
