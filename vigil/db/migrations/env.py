"""Alembic environment. DSN comes from Vault/env via Settings — never from alembic.ini."""

from __future__ import annotations

import os

from alembic import context
from sqlalchemy import engine_from_config, pool

from vigil.core.config import get_settings
from vigil.db.base import Base
from vigil.db import models  # noqa: F401  (register tables on Base.metadata)

config = context.config
target_metadata = Base.metadata


def _dsn() -> str:
    """Migrations run as the OWNER/admin (DDL + RLS policy creation), not the app role.

    In production the admin DSN comes from Vault; locally/CI ``VIGIL_DB_ADMIN_DSN`` selects
    the owning role. The app's least-privilege role (vigil_app) must NOT own tables, or it
    could ALTER away RLS. Falls back to the app DSN when no admin DSN is configured.
    """
    return os.environ.get("VIGIL_DB_ADMIN_DSN") or get_settings().db_dsn


def run_migrations_offline() -> None:
    context.configure(
        url=_dsn(),
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    section = config.get_section(config.config_ini_section, {})
    section["sqlalchemy.url"] = _dsn()
    connectable = engine_from_config(
        section, prefix="sqlalchemy.", poolclass=pool.NullPool
    )
    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()
