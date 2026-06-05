"""ORM models (domain.md).

Tenancy contract enforced here + in the migration:
- Every TENANT-scoped table carries ``sponsor_id`` (NOT NULL, FK) and gets the default
  sponsor RLS policy in the creating migration: visible only when
  ``sponsor_id = current_setting('app.current_sponsor')::uuid``.
- RLS-EXEMPT tables are justified inline below (matching domain.md's exemption list).

Models declare structure only. RLS policies are DDL and live in the Alembic migration; the
list :data:`TENANT_TABLES` is the single source of truth the migration iterates over so a
new tenant table cannot be added without RLS.
"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    Float,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from vigil.db.base import Base, created_at, pk, sponsor_fk

# Tenant tables that MUST carry sponsor_id + the default RLS policy. The migration reads
# this list; adding a tenant table here without RLS is impossible by construction.
TENANT_TABLES: tuple[str, ...] = ("trial", "site", "participant", "intervention")


# --------------------------------------------------------------------------- tenant root
class Sponsor(Base):
    """Tenant root. RLS-exempt from the *default* predicate: keyed on its own ``id`` so a
    caller sees only their own sponsor row (domain.md). Policy added in the migration."""

    __tablename__ = "sponsor"

    id: Mapped[uuid.UUID] = pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = created_at()


# ------------------------------------------------------------------- global / reference
class Cro(Base):
    """Global CRO registry. No per-sponsor data → RLS-exempt (domain.md)."""

    __tablename__ = "cro"

    id: Mapped[uuid.UUID] = pk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = created_at()


# ----------------------------------------------------------------------- tenant tables
class Trial(Base):
    __tablename__ = "trial"

    id: Mapped[uuid.UUID] = pk()
    sponsor_id: Mapped[uuid.UUID] = sponsor_fk()
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = created_at()


class Site(Base):
    __tablename__ = "site"

    id: Mapped[uuid.UUID] = pk()
    sponsor_id: Mapped[uuid.UUID] = sponsor_fk()
    trial_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("trial.id", ondelete="RESTRICT"),
        nullable=False,
    )
    name: Mapped[str] = mapped_column(String(200), nullable=False)
    created_at: Mapped[datetime] = created_at()


class Participant(Base):
    """Coded participant record. Identities are held only for site roles; the spine stores
    the coded slice + an optional identity blob gated at the service layer."""

    __tablename__ = "participant"

    id: Mapped[uuid.UUID] = pk()
    sponsor_id: Mapped[uuid.UUID] = sponsor_fk()
    trial_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("trial.id", ondelete="RESTRICT"),
        nullable=False,
    )
    site_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("site.id", ondelete="RESTRICT"),
        nullable=False,
    )
    coded_ref: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False, default="active")
    risk_score: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    risk_band: Mapped[str] = mapped_column(String(8), nullable=False, default="low")
    enrolled_at: Mapped[datetime] = created_at()
    created_at: Mapped[datetime] = created_at()


class Intervention(Base):
    __tablename__ = "intervention"

    id: Mapped[uuid.UUID] = pk()
    sponsor_id: Mapped[uuid.UUID] = sponsor_fk()
    participant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("participant.id", ondelete="CASCADE"),
        nullable=False,
    )
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    note: Mapped[str] = mapped_column(Text, nullable=False, default="")
    actor_user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("user.id", ondelete="RESTRICT"),
        nullable=False,
    )
    created_at: Mapped[datetime] = created_at()


# --------------------------------------------------- cross-tenant by design (bespoke RLS)
class User(Base):
    """Cross-tenant by design (CRO spans sponsors; platform spans all) → no single-sponsor
    predicate. Exactly one of home_sponsor_id / home_cro_id is set, or neither (platform).
    """

    __tablename__ = "user"

    id: Mapped[uuid.UUID] = pk()
    email: Mapped[str] = mapped_column(String(320), nullable=False, unique=True)
    password_hash: Mapped[str] = mapped_column(String(256), nullable=False)
    role: Mapped[str] = mapped_column(String(32), nullable=False)
    home_sponsor_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("sponsor.id", ondelete="RESTRICT"),
        nullable=True,
    )
    home_cro_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("cro.id", ondelete="RESTRICT"), nullable=True
    )
    # Site users carry their fixed trial/site so scope resolution is deterministic.
    home_trial_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("trial.id", ondelete="RESTRICT"),
        nullable=True,
    )
    home_site_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("site.id", ondelete="RESTRICT"), nullable=True
    )
    scope_ver: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = created_at()


class AssignmentGrant(Base):
    """Single source of truth for CRO scope (domain.md). One row = one granted tuple."""

    __tablename__ = "assignment_grant"
    __table_args__ = (
        UniqueConstraint(
            "user_id", "sponsor_id", "trial_id", "site_id", name="uq_grant_tuple"
        ),
    )

    id: Mapped[uuid.UUID] = pk()
    user_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("user.id", ondelete="CASCADE"), nullable=False
    )
    sponsor_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("sponsor.id", ondelete="RESTRICT"),
        nullable=False,
    )
    trial_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("trial.id", ondelete="RESTRICT"),
        nullable=True,
    )
    site_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("site.id", ondelete="RESTRICT"), nullable=True
    )
    granted_by: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("user.id", ondelete="RESTRICT"),
        nullable=False,
    )
    granted_at: Mapped[datetime] = created_at()


class AuditLog(Base):
    """Cross-tenant by design (auditor/admin read across) → bespoke role-scoped policy.
    Every user creation, grant, revoke, and scope change writes a row here."""

    __tablename__ = "audit_log"

    id: Mapped[uuid.UUID] = pk()
    actor_user_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True), ForeignKey("user.id", ondelete="SET NULL"), nullable=True
    )
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    target_type: Mapped[str] = mapped_column(String(64), nullable=False)
    target_id: Mapped[str | None] = mapped_column(String(64), nullable=True)
    sponsor_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("sponsor.id", ondelete="SET NULL"),
        nullable=True,
    )
    detail: Mapped[dict] = mapped_column(JSONB, nullable=False, default=dict)
    created_at: Mapped[datetime] = created_at()
