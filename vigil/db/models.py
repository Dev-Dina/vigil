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
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from pgvector.sqlalchemy import Vector
from sqlalchemy.dialects.postgresql import ARRAY, JSONB
from sqlalchemy.dialects.postgresql import UUID as PG_UUID
from sqlalchemy.orm import Mapped, mapped_column

from vigil.db.base import Base, created_at, pk, sponsor_fk

# Embedding dimension for DocumentChunk.embedding — kept in sync with
# vigil.agents.embeddings.EMBED_DIM (matches all-MiniLM-L6-v2 / bge-small-en-v1.5). Declared
# here (not imported) so the ORM does not depend on the agent layer.
_EMBED_DIM = 384

# Tenant tables that MUST carry sponsor_id + the default RLS policy. The migration reads
# this list; adding a tenant table here without RLS is impossible by construction.
TENANT_TABLES: tuple[str, ...] = (
    "trial",
    "site",
    "participant",
    "engagement",
    "intervention",
    "participant_score",
    "risk_crossing",
)


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
    # Trial-level static context for LSTM scoring (B2b). planned_duration_days is
    # intentionally here, NOT on participant (spec B2a-spec-2 decision c).
    n_sites: Mapped[int | None] = mapped_column(Integer, nullable=True)
    planned_duration_days: Mapped[int | None] = mapped_column(Integer, nullable=True)
    phase: Mapped[str | None] = mapped_column(String(64), nullable=True)


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
    # Clinical covariates added in B2a-1. Nullable: existing rows have no recorded values.
    # Imputed-capable columns travel with their companion *_baseline_imputed flag.
    # True = imputed (literature-prior); False = real measured value from source record.
    # sex is always-real categorical; no companion flag (scoring.md § Static covariates home).
    age_years: Mapped[float | None] = mapped_column(Float, nullable=True)
    age_years_baseline_imputed: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True
    )
    hba1c_pct: Mapped[float | None] = mapped_column(Float, nullable=True)
    hba1c_pct_baseline_imputed: Mapped[bool | None] = mapped_column(
        Boolean, nullable=True
    )
    bmi: Mapped[float | None] = mapped_column(Float, nullable=True)
    bmi_baseline_imputed: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    sex: Mapped[str | None] = mapped_column(String(16), nullable=True)
    # Per-arm context added in B2b (arm_type is per-participant / per-arm; no companion flag).
    arm_type: Mapped[str | None] = mapped_column(String(64), nullable=True)


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
    # Phase 9 (9.5): the address the PII-free serious-risk doorbell routes to. The login `email`
    # is the identity; this is a separate, user-settable notification target. Nullable, defaults
    # UNSET; set per user via PUT /me/notification-email (domain.md § Notification routing).
    notification_email: Mapped[str | None] = mapped_column(String(320), nullable=True)
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


# ---------------------------------------------------------------- scoring writeback
class ParticipantScore(Base):
    """Scoring writeback — tenant-scoped, RLS on sponsor_id (no platform bypass).

    APPENDED history: one row per (participant_id, model_version, scoring run). Identity is
    the surrogate ``id`` PK; rows are ordered by ``computed_at`` (no UNIQUE on
    participant_id+model_version — that forced upsert-overwrite). Clinical reads surface the
    NEWEST champion row (see specs/scoring.md § Writeback, routing.md § (i)).
    synthetic=True on every row from the demo/synthetic cohort.
    """

    __tablename__ = "participant_score"
    __table_args__ = (
        Index(
            "ix_ps_participant_version_time",
            "participant_id",
            "model_version",
            "computed_at",
        ),
    )

    id: Mapped[uuid.UUID] = pk()
    participant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("participant.id", ondelete="CASCADE"),
        nullable=False,
    )
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
    risk_score: Mapped[float] = mapped_column(Float, nullable=False)
    risk_band: Mapped[str] = mapped_column(String(8), nullable=False)
    top_factors: Mapped[list] = mapped_column(ARRAY(Text), nullable=False, default=list)
    reasons: Mapped[dict] = mapped_column(JSONB, nullable=False, default=list)
    model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    model_card_ref: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    computed_at: Mapped[datetime] = created_at()


# ------------------------------------------------ serious-risk crossing (Phase 9, Gate 9.2)
class RiskCrossing(Base):
    """A serious-risk CROSSING: a champion-point transition from non-high → high (Phase 9).

    Tenant-scoped (sponsor_id, RLS — same guarantee as participant_score). Detected worker-side
    right after the score writeback, when the worker knows the PRIOR champion band. Idempotent by
    construction: ``crossing_score_id`` is UNIQUE (the champion score row that crossed is the
    anchor), and the transition is only recorded when the prior champion band was non-high — so a
    re-score while still high, and a retried job (whose prior band is now high), add NO new
    crossing. The row carries the participant's full (sponsor, trial, site) scope so Gate 9.5 can
    resolve recipients SCOPE-BOUND; ``notified`` is the dedupe hook the 9.6 email claims. No email
    is sent here (Gate 9.6).
    """

    __tablename__ = "risk_crossing"
    __table_args__ = (
        UniqueConstraint("crossing_score_id", name="uq_risk_crossing_score"),
        Index("ix_risk_crossing_sponsor_site", "sponsor_id", "site_id"),
        Index("ix_risk_crossing_notified", "notified"),
    )

    id: Mapped[uuid.UUID] = pk()
    sponsor_id: Mapped[uuid.UUID] = sponsor_fk()
    participant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("participant.id", ondelete="CASCADE"),
        nullable=False,
    )
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
    # The champion score row whose append produced the crossing — UNIQUE → idempotent anchor.
    crossing_score_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("participant_score.id", ondelete="CASCADE"),
        nullable=False,
    )
    model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    risk_score: Mapped[float] = mapped_column(Float, nullable=False)
    risk_band: Mapped[str] = mapped_column(String(8), nullable=False)  # always 'high'
    prior_band: Mapped[str] = mapped_column(
        String(8), nullable=False
    )  # the non-high band crossed FROM: low|medium|none
    synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    notified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    detected_at: Mapped[datetime] = created_at()


# ---------------------------------------------------------------- engagement (B2a-1)
class Engagement(Base):
    """Per-participant visit trajectory rows backing the LSTM sequence model.

    Tenant-scoped (sponsor_id on every row), RLS-enforced, NOT RLS-exempt.
    Unique key: (participant_id, visit_index) — one row per participant per visit slot.
    synthetic=True on rows from the demo injection path or synthetic T2D parquet.
    See specs/scoring.md § Engagement (visit trajectory) input.
    """

    __tablename__ = "engagement"
    __table_args__ = (
        UniqueConstraint(
            "participant_id", "visit_index", name="uq_engagement_participant_visit"
        ),
    )

    id: Mapped[uuid.UUID] = pk()
    sponsor_id: Mapped[uuid.UUID] = sponsor_fk()
    participant_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("participant.id", ondelete="CASCADE"),
        nullable=False,
    )
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
    visit_index: Mapped[int] = mapped_column(Integer, nullable=False)
    visit_timestamp: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    attended: Mapped[bool] = mapped_column(Boolean, nullable=False)
    missed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    cumulative_missed: Mapped[int] = mapped_column(Integer, nullable=False)
    consecutive_missed: Mapped[int] = mapped_column(Integer, nullable=False)
    synthetic: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)


# ------------------------------------------------------ platform / global routing
class RoutingState(Base):
    """Platform-scoped model routing registry. RLS-exempt: global infrastructure tier.

    No sponsor_id — routing decisions apply across all tenants.
    RLS-exempt per domain.md: routing tables are global infrastructure.
    Sponsor sessions blocked at app layer. See migration 0003_routing_state.
    """

    __tablename__ = "routing_state"
    __table_args__ = (
        UniqueConstraint("regime", "role", name="uq_routing_state_regime_role"),
    )

    id: Mapped[uuid.UUID] = pk()
    regime: Mapped[str] = mapped_column(String(128), nullable=False)
    role: Mapped[str] = mapped_column(String(16), nullable=False)
    model_version: Mapped[str] = mapped_column(String(128), nullable=False)
    model_card_ref: Mapped[str] = mapped_column(String(512), nullable=False, default="")
    health: Mapped[str] = mapped_column(String(16), nullable=False, default="healthy")
    promoted_at: Mapped[datetime] = created_at()
    promoted_by: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("user.id", ondelete="SET NULL"),
        nullable=True,
    )


# ------------------------------------------------- observability (message_events, Phase 5)
class MessageEvent(Base):
    """One row per chatbot/assistant turn on BOTH surfaces (specs/observability.md).

    Cross-tenant BY ROLE — same class as ``audit_log`` (domain.md "Cross-tenant by design"):
    a CRO spans sponsors and the auditor/admin read across, so no single-sponsor predicate
    fits. ``sponsor_id`` is NULLABLE — Guide/platform turns are null-sponsor, visible only to
    platform/auditor (the audit_scope-style policy in migration 0007), never to a sponsor
    session. NOT in TENANT_TABLES (it does not get the default sponsor-only policy).

    WRITE-PATH CONTRACT (Phase 5, Gate 5.1): content arrives ALREADY REDACTED — the redaction
    LOGIC (fail-loud, before the LLM) is Gate 5.2. This model stores only the redacted columns;
    there is no raw-content column, so raw text cannot be persisted by construction. The
    admin/inspect surface (GET /monitoring/messages) is Phase 6.
    """

    __tablename__ = "message_events"
    __table_args__ = (
        Index("ix_msg_conversation_ts", "conversation_id", "ts"),
        Index("ix_msg_sponsor_ts", "sponsor_id", "ts"),
    )

    id: Mapped[uuid.UUID] = pk()
    conversation_id: Mapped[uuid.UUID] = mapped_column(
        PG_UUID(as_uuid=True), nullable=False
    )
    request_id: Mapped[str] = mapped_column(String(64), nullable=False)
    # NULL = Guide/platform turn (cross-tenant-by-role, like audit_log). FK SET NULL on delete.
    sponsor_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("sponsor.id", ondelete="SET NULL"),
        nullable=True,
    )
    role_or_guest_scope: Mapped[str] = mapped_column(String(64), nullable=False)
    surface: Mapped[str] = mapped_column(
        String(16), nullable=False
    )  # local_assistant|public_guide
    route_or_agent: Mapped[str] = mapped_column(String(128), nullable=False, default="")
    guardrail_decision: Mapped[str] = mapped_column(
        String(16), nullable=False
    )  # allowed|blocked
    retrieved_chunks: Mapped[list] = mapped_column(JSONB, nullable=False, default=list)
    llm_provider_model: Mapped[str] = mapped_column(
        String(128), nullable=False, default=""
    )
    latency_ms: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    token_cost_estimate: Mapped[float] = mapped_column(
        Numeric(12, 6), nullable=False, default=0
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)  # ok|refused|error
    redacted_user_msg: Mapped[str] = mapped_column(Text, nullable=False, default="")
    redacted_assistant_msg: Mapped[str] = mapped_column(
        Text, nullable=False, default=""
    )
    ts: Mapped[datetime] = created_at()


# --------------------------------------------------- RAG vector corpus (document_chunk, Phase 5)
class DocumentChunk(Base):
    """A retrievable, embedded chunk of a grounding document (specs/rag.md § Retrieval stack).

    Corpus-scope decision (a) — GLOBAL-OR-SPONSOR, RLS-ready (flagged for ratification):
    ``sponsor_id`` is NULLABLE. A NULL row is a GLOBAL project doc (the model cards + PHASE3_CARD,
    the Phase-5 doc-grounding corpus) readable by every caller's agent. A non-null row is
    sponsor-scoped (the DEFERRED protocol/SOP docs) and follows the strict ``participant_score``
    pattern — fail-closed, no cross-tenant read. The single policy (migration 0008) admits
    ``sponsor_id IS NULL OR sponsor_id = current_sponsor OR is_platform``, so adding sponsor-scoped
    docs later needs NO RLS re-architecture.

    ``embedding`` is a LOCAL embedding (vigil.agents.embeddings, hashing default / ST optional),
    dim ``EMBED_DIM`` (384) — backend-swappable with no migration. The pgvector ``Vector`` type
    is a light pure-Python import (no torch); the HEAVY ``sentence_transformers`` backend is what
    stays lazy/out of the light/golden import path (vigil.agents.embeddings).
    """

    __tablename__ = "document_chunk"
    __table_args__ = (
        Index("ix_docchunk_source", "source_ref", "chunk_index"),
        Index("ix_docchunk_sponsor", "sponsor_id"),
    )

    id: Mapped[uuid.UUID] = pk()
    # NULL = global project doc (cards); set = sponsor-scoped (deferred protocol/SOP docs).
    sponsor_id: Mapped[uuid.UUID | None] = mapped_column(
        PG_UUID(as_uuid=True),
        ForeignKey("sponsor.id", ondelete="CASCADE"),
        nullable=True,
    )
    source_ref: Mapped[str] = mapped_column(
        String(512), nullable=False
    )  # card/file path
    chunk_index: Mapped[int] = mapped_column(Integer, nullable=False)
    chunk_text: Mapped[str] = mapped_column(Text, nullable=False)
    embedding: Mapped[list[float]] = mapped_column(Vector(_EMBED_DIM), nullable=False)
    created_at: Mapped[datetime] = created_at()
