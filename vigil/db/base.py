"""SQLAlchemy declarative base + shared column helpers.

The RLS session variable is ``app.current_sponsor`` (a UUID). It is set per request by the
scoped session opener (repositories layer); models here only declare structure.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

# Name of the Postgres GUC RLS policies read. Application sets it per request from the JWT.
RLS_SPONSOR_GUC = "app.current_sponsor"


class Base(DeclarativeBase):
    pass


def pk() -> Mapped[uuid.UUID]:
    return mapped_column(PG_UUID(as_uuid=True), primary_key=True, default=uuid.uuid4)


def sponsor_fk() -> Mapped[uuid.UUID]:
    """The tenant key present on EVERY tenant-scoped table (NOT NULL, FK to sponsor)."""
    return mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("sponsor.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )


def created_at() -> Mapped[datetime]:
    return mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
