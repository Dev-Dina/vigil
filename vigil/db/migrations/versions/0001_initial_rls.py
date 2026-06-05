"""initial schema with row-level security (Phase 2 spine)

Tenancy is enforced by the database, not by application WHERE clauses (domain.md). Every
tenant-scoped table carries ``sponsor_id`` and gets a sponsor RLS policy IN THIS MIGRATION
(schema-migration skill — never added later). Policies are fail-closed: an unset GUC reads
as NULL and matches no rows.

Session GUCs (set per request by the scoped session opener from the verified JWT):
- ``app.current_sponsor``  : uuid of the sponsor the request is scoped to (NULL → see nothing)
- ``app.is_platform``      : 'on' for platform users (admin/auditor) — cross-tenant read of
                              the bespoke tables only (user/assignment_grant/audit_log).

``FORCE ROW LEVEL SECURITY`` is set on every secured table so the policy binds even when the
app connects as the table owner (true in local dev / the leakage test). Production additionally
connects via Vault-issued least-privilege creds.

Revision ID: 0001_initial_rls
Revises:
Create Date: 2026-06-05
"""

from __future__ import annotations

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql as pg

revision = "0001_initial_rls"
down_revision = None
branch_labels = None
depends_on = None

# Tenant tables: sponsor_id column + default sponsor policy.
TENANT_TABLES = ("trial", "site", "participant", "intervention")


def _uuid_pk() -> sa.Column:
    return sa.Column(
        "id",
        pg.UUID(as_uuid=True),
        primary_key=True,
        server_default=sa.text("gen_random_uuid()"),
    )


def _ts(name: str = "created_at") -> sa.Column:
    return sa.Column(
        name,
        sa.DateTime(timezone=True),
        nullable=False,
        server_default=sa.text("now()"),
    )


def upgrade() -> None:
    op.execute('CREATE EXTENSION IF NOT EXISTS "pgcrypto"')

    # ---------------------------------------------------------------- tenant root + refs
    op.create_table(
        "sponsor",
        _uuid_pk(),
        sa.Column("name", sa.String(200), nullable=False),
        _ts(),
    )
    op.create_table(
        "cro",
        _uuid_pk(),
        sa.Column("name", sa.String(200), nullable=False),
        _ts(),
    )

    # ---------------------------------------------------------------------- tenant tables
    op.create_table(
        "trial",
        _uuid_pk(),
        sa.Column(
            "sponsor_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("sponsor.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        _ts(),
    )
    op.create_index("ix_trial_sponsor_id", "trial", ["sponsor_id"])

    op.create_table(
        "site",
        _uuid_pk(),
        sa.Column(
            "sponsor_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("sponsor.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "trial_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("trial.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("name", sa.String(200), nullable=False),
        _ts(),
    )
    op.create_index("ix_site_sponsor_id", "site", ["sponsor_id"])

    # user is referenced by intervention/audit/grant; create it before those FKs.
    op.create_table(
        "user",
        _uuid_pk(),
        sa.Column("email", sa.String(320), nullable=False, unique=True),
        sa.Column("password_hash", sa.String(256), nullable=False),
        sa.Column("role", sa.String(32), nullable=False),
        sa.Column(
            "home_sponsor_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("sponsor.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "home_cro_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("cro.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "home_trial_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("trial.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "home_site_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("site.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column("scope_ver", sa.Integer, nullable=False, server_default="1"),
        sa.Column("is_active", sa.Boolean, nullable=False, server_default=sa.true()),
        _ts(),
    )

    op.create_table(
        "participant",
        _uuid_pk(),
        sa.Column(
            "sponsor_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("sponsor.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "trial_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("trial.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "site_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("site.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column("coded_ref", sa.String(64), nullable=False),
        sa.Column("status", sa.String(16), nullable=False, server_default="active"),
        sa.Column("risk_score", sa.Float, nullable=False, server_default="0"),
        sa.Column("risk_band", sa.String(8), nullable=False, server_default="low"),
        _ts("enrolled_at"),
        _ts(),
    )
    op.create_index("ix_participant_sponsor_id", "participant", ["sponsor_id"])

    op.create_table(
        "intervention",
        _uuid_pk(),
        sa.Column(
            "sponsor_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("sponsor.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "participant_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("participant.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("kind", sa.String(32), nullable=False),
        sa.Column("note", sa.Text, nullable=False, server_default=""),
        sa.Column(
            "actor_user_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("user.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        _ts(),
    )
    op.create_index("ix_intervention_sponsor_id", "intervention", ["sponsor_id"])

    # ----------------------------------------------- cross-tenant by design (bespoke RLS)
    op.create_table(
        "assignment_grant",
        _uuid_pk(),
        sa.Column(
            "user_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("user.id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column(
            "sponsor_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("sponsor.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        sa.Column(
            "trial_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("trial.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "site_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("site.id", ondelete="RESTRICT"),
            nullable=True,
        ),
        sa.Column(
            "granted_by",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("user.id", ondelete="RESTRICT"),
            nullable=False,
        ),
        _ts("granted_at"),
        sa.UniqueConstraint(
            "user_id", "sponsor_id", "trial_id", "site_id", name="uq_grant_tuple"
        ),
    )

    op.create_table(
        "audit_log",
        _uuid_pk(),
        sa.Column(
            "actor_user_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("user.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("action", sa.String(64), nullable=False),
        sa.Column("target_type", sa.String(64), nullable=False),
        sa.Column("target_id", sa.String(64), nullable=True),
        sa.Column(
            "sponsor_id",
            pg.UUID(as_uuid=True),
            sa.ForeignKey("sponsor.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column(
            "detail", pg.JSONB, nullable=False, server_default=sa.text("'{}'::jsonb")
        ),
        _ts(),
    )

    _enable_rls()


def _enable_rls() -> None:
    # NULLIF(..., '') so an unset/empty GUC casts to NULL (matches nothing — fail-closed)
    # rather than raising a uuid cast error; this also lets the is_platform OR-branch win
    # without Postgres evaluating ''::uuid.
    sponsor_guc = "NULLIF(current_setting('app.current_sponsor', true), '')"
    is_platform = "current_setting('app.is_platform', true) = 'on'"

    # --- tenant root: a caller sees only their own sponsor row (or platform sees all) ---
    op.execute("ALTER TABLE sponsor ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE sponsor FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY sponsor_self ON sponsor
        USING (id = {sponsor_guc}::uuid OR {is_platform})
        WITH CHECK (id = {sponsor_guc}::uuid OR {is_platform})
        """
    )

    # --- default tenant policy: sponsor_id must match the session GUC (fail-closed) ---
    for table in TENANT_TABLES:
        op.execute(f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY")
        op.execute(f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY")
        op.execute(
            f"""
            CREATE POLICY {table}_sponsor_isolation ON {table}
            USING (sponsor_id = {sponsor_guc}::uuid)
            WITH CHECK (sponsor_id = {sponsor_guc}::uuid)
            """
        )

    # --- user: visible if it belongs to the current sponsor, or to platform callers ---
    op.execute('ALTER TABLE "user" ENABLE ROW LEVEL SECURITY')
    op.execute('ALTER TABLE "user" FORCE ROW LEVEL SECURITY')
    op.execute(
        f"""
        CREATE POLICY user_scope ON "user"
        USING (
            {is_platform}
            OR home_sponsor_id = {sponsor_guc}::uuid
            OR id IN (
                SELECT user_id FROM assignment_grant
                WHERE sponsor_id = {sponsor_guc}::uuid
            )
        )
        WITH CHECK (
            {is_platform}
            OR home_sponsor_id = {sponsor_guc}::uuid
        )
        """
    )

    # --- assignment_grant: rows for the current sponsor, or platform sees all ---
    op.execute("ALTER TABLE assignment_grant ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE assignment_grant FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY grant_scope ON assignment_grant
        USING ({is_platform} OR sponsor_id = {sponsor_guc}::uuid)
        WITH CHECK ({is_platform} OR sponsor_id = {sponsor_guc}::uuid)
        """
    )

    # --- audit_log: current sponsor's rows, or platform (auditor/admin) reads all ---
    op.execute("ALTER TABLE audit_log ENABLE ROW LEVEL SECURITY")
    op.execute("ALTER TABLE audit_log FORCE ROW LEVEL SECURITY")
    op.execute(
        f"""
        CREATE POLICY audit_scope ON audit_log
        USING ({is_platform} OR sponsor_id = {sponsor_guc}::uuid)
        WITH CHECK ({is_platform} OR sponsor_id = {sponsor_guc}::uuid)
        """
    )


def downgrade() -> None:
    for table in (
        "audit_log",
        "assignment_grant",
        "intervention",
        "participant",
        "site",
        "trial",
    ):
        op.drop_table(table)
    op.drop_table("user")
    op.drop_table("cro")
    op.drop_table("sponsor")
