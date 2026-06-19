"""The Guide's OWN ``message_events`` sink (specs/observability.md schema, specs/isolation.md §).

SAME logical schema as the app's ``message_events`` (so guardrail firing is provable the same
way), but a SEPARATE model in the Guide's OWN store — this module does NOT import
``vigil.db.models`` and never writes to the app Postgres. Portable column types so the default
store is a Guide-owned SQLite file (no shared datastore); a Guide-owned Postgres is a Phase-8 swap.

For the Guide every row is a guest turn: ``surface = "public_guide"`` and ``sponsor_id`` is always
NULL (cross-tenant-by-role, like the app's audit rows). There is no raw-content column — only the
redacted fields — so raw text cannot be persisted by construction. Real turns are written in 7.3.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    JSON,
    DateTime,
    Engine,
    Float,
    Integer,
    String,
    Text,
    create_engine,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, Session, mapped_column


class GuideBase(DeclarativeBase):
    """The Guide's OWN declarative base — distinct from the app's ``vigil.db`` metadata."""


class GuideMessageEvent(GuideBase):
    """One redacted turn row in the Guide's OWN sink. Same fields as observability.md."""

    __tablename__ = "message_events"

    id: Mapped[str] = mapped_column(
        String(36), primary_key=True, default=lambda: str(uuid.uuid4())
    )
    conversation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # Always NULL for the Guide (guest turn) — kept for schema parity with the app sink.
    sponsor_id: Mapped[str | None] = mapped_column(
        String(36), nullable=True, default=None
    )
    role_or_guest_scope: Mapped[str] = mapped_column(
        String(64), nullable=False, default="guest"
    )
    surface: Mapped[str] = mapped_column(
        String(16), nullable=False, default="public_guide"
    )
    route_or_agent: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    guardrail_decision: Mapped[str] = mapped_column(String(16), nullable=False)
    retrieved_chunks: Mapped[list] = mapped_column(JSON, nullable=False, default=list)
    llm_provider_model: Mapped[str] = mapped_column(
        String(128), nullable=False, default=""
    )
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    token_cost_estimate: Mapped[float] = mapped_column(
        Float, nullable=False, default=0.0
    )
    # Gate GUIDE-COST: REAL per-turn token usage (summed across the topical-classify + answer calls).
    # init_sink/create_all materializes these on a FRESH sink (it does NOT ALTER an existing SQLite
    # table) — a container recreate gives the fresh sink; a Guide-owned Postgres is the Phase-8 swap.
    prompt_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    completion_tokens: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    redacted_user_msg: Mapped[str] = mapped_column(Text, nullable=False, default="")
    redacted_assistant_msg: Mapped[str] = mapped_column(
        Text, nullable=False, default=""
    )
    ts: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        default=lambda: datetime.now(tz=timezone.utc),
    )


def create_sink_engine(dsn: str) -> Engine:
    """Create the engine for the Guide's OWN sink (its own store, never the app Postgres)."""
    return create_engine(dsn)


def init_sink(engine: Engine) -> None:
    """Create the Guide's ``message_events`` table in its own store (idempotent)."""
    GuideBase.metadata.create_all(engine)


def write_message_event(
    session: Session,
    *,
    conversation_id: str,
    request_id: str,
    guardrail_decision: str,
    status: str,
    redacted_user_msg: str = "",
    redacted_assistant_msg: str = "",
    route_or_agent: str = "",
    retrieved_chunks: list[Any] | None = None,
    llm_provider_model: str = "",
    latency_ms: int = 0,
    token_cost_estimate: float = 0.0,
    prompt_tokens: int = 0,
    completion_tokens: int = 0,
) -> GuideMessageEvent:
    """Append one redacted Guide turn row. ``surface``/``sponsor_id`` are fixed (guest/NULL).

    Text MUST already be redacted (there is no raw column). Real turns call this in Gate 7.3.
    """
    row = GuideMessageEvent(
        conversation_id=conversation_id,
        request_id=request_id,
        sponsor_id=None,
        role_or_guest_scope="guest",
        surface="public_guide",
        route_or_agent=route_or_agent,
        guardrail_decision=guardrail_decision,
        retrieved_chunks=retrieved_chunks if retrieved_chunks is not None else [],
        llm_provider_model=llm_provider_model,
        latency_ms=latency_ms,
        token_cost_estimate=token_cost_estimate,
        prompt_tokens=prompt_tokens,
        completion_tokens=completion_tokens,
        status=status,
        redacted_user_msg=redacted_user_msg,
        redacted_assistant_msg=redacted_assistant_msg,
    )
    session.add(row)
    session.flush()
    return row


@dataclass(frozen=True, slots=True)
class GuideCostRollup:
    """Per-model aggregate over the Guide's OWN sink (no rows/content — totals only)."""

    llm_provider_model: str
    turns: int
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    total_cost: float
    total_latency_ms: int
    avg_latency_ms: float


@dataclass(frozen=True, slots=True)
class GuideCostSummary:
    """Aggregate cost/usage over the Guide's OWN sink — the public-aggregate /metrics/cost payload.

    Totals + per-model rollups ONLY: never a row, never question/answer content, never PII (the
    Guide holds none). Mirrors the app's cost_report SHAPE so the dashboard can consume both.
    """

    surface: str
    total_turns: int
    total_prompt_tokens: int
    total_completion_tokens: int
    total_tokens: int
    total_cost: float
    avg_latency_ms: float
    rollups: list[GuideCostRollup]


def cost_summary(session: Session) -> GuideCostSummary:
    """Aggregate the Guide's OWN sink into totals + per-model rollups (read-only; no rows exposed)."""
    from sqlalchemy import func, select

    ev = GuideMessageEvent
    rollups: list[GuideCostRollup] = []
    rows = session.execute(
        select(
            ev.llm_provider_model,
            func.count(ev.id),
            func.coalesce(func.sum(ev.prompt_tokens), 0),
            func.coalesce(func.sum(ev.completion_tokens), 0),
            func.coalesce(func.sum(ev.token_cost_estimate), 0.0),
            func.coalesce(func.sum(ev.latency_ms), 0),
        ).group_by(ev.llm_provider_model)
    ).all()
    for model, turns, p_tok, c_tok, cost, latency in rows:
        rollups.append(
            GuideCostRollup(
                llm_provider_model=model or "",
                turns=int(turns),
                prompt_tokens=int(p_tok),
                completion_tokens=int(c_tok),
                total_tokens=int(p_tok) + int(c_tok),
                total_cost=round(float(cost), 6),
                total_latency_ms=int(latency),
                avg_latency_ms=round(int(latency) / int(turns), 2) if turns else 0.0,
            )
        )
    total_turns = sum(r.turns for r in rollups)
    total_latency = sum(r.total_latency_ms for r in rollups)
    return GuideCostSummary(
        surface="public_guide",
        total_turns=total_turns,
        total_prompt_tokens=sum(r.prompt_tokens for r in rollups),
        total_completion_tokens=sum(r.completion_tokens for r in rollups),
        total_tokens=sum(r.total_tokens for r in rollups),
        total_cost=round(sum(r.total_cost for r in rollups), 6),
        avg_latency_ms=round(total_latency / total_turns, 2) if total_turns else 0.0,
        rollups=rollups,
    )
