"""Backend-spine test harness: real Postgres (RLS is not mockable), migrate, seed.

The leakage test needs a live Postgres because row-level security is a database feature. The
reachability/migration DSN comes from ``VIGIL_DB_ADMIN_DSN`` (CI provides a Postgres service;
locally a docker container). If no Postgres is reachable, the DB-backed tests skip — but CI
runs them for real, which is where the sacred test gates.

Secrets use the env backend so no Vault is needed in tests; the signing key is a throwaway.
"""

from __future__ import annotations

import os

import pytest
from sqlalchemy import text

# Force the env secrets backend + deterministic config before any vigil import that reads it.
# The ADMIN dsn (superuser) owns the schema + runs migrations; the APP dsn (vigil_app, a
# non-superuser NOBYPASSRLS role) is what the engine/seed/tests connect as so RLS actually
# binds. A superuser connection would silently bypass RLS and the leakage test would be a lie.
os.environ.setdefault("VIGIL_SECRETS_BACKEND", "env")
os.environ.setdefault("VIGIL_JWT_SIGNING_KEY", "test-signing-key-not-a-secret")
os.environ.setdefault("VIGIL_LLM_API_KEY", "test-llm-key")
# Hermetic LLM: force the StubLLMClient so no test makes a live OpenRouter call (no key needed).
os.environ.setdefault("VIGIL_LLM_STUB", "true")

_ADMIN_DSN = os.environ.get(
    "VIGIL_DB_ADMIN_DSN", "postgresql+psycopg://vigil:vigil@localhost:55432/vigil"
)
_APP_PASSWORD = os.environ.get("VIGIL_APP_DB_PASSWORD", "vigil_app_pw")
os.environ.setdefault("VIGIL_DB_ADMIN_DSN", _ADMIN_DSN)
os.environ.setdefault(
    "VIGIL_DB_DSN",
    f"postgresql+psycopg://vigil_app:{_APP_PASSWORD}@localhost:55432/vigil",
)
os.environ.setdefault("VIGIL_REDIS_URL", "redis://localhost:6379/15")


def _admin_reachable() -> bool:
    from sqlalchemy import create_engine

    try:
        eng = create_engine(_ADMIN_DSN)
        with eng.connect() as conn:
            conn.execute(text("SELECT 1"))
        return True
    except Exception:  # noqa: BLE001 - any failure → skip DB tests
        return False


def _bootstrap_app_role() -> None:
    """Create the least-privilege app role + grants (idempotent), as the admin/superuser."""
    from sqlalchemy import create_engine

    eng = create_engine(_ADMIN_DSN, isolation_level="AUTOCOMMIT")
    # _APP_PASSWORD is a controlled test constant, not user input.
    with eng.connect() as conn:
        conn.execute(
            text(
                "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname='vigil_app') "
                f"THEN CREATE ROLE vigil_app LOGIN PASSWORD '{_APP_PASSWORD}' NOSUPERUSER "
                "NOBYPASSRLS NOCREATEDB NOCREATEROLE; END IF; END $$;"
            )
        )
        conn.execute(text(f"ALTER ROLE vigil_app PASSWORD '{_APP_PASSWORD}'"))
        conn.execute(text("GRANT USAGE ON SCHEMA public TO vigil_app"))
        conn.execute(
            text(
                "GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA public TO vigil_app"
            )
        )
        conn.execute(
            text("GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA public TO vigil_app")
        )


@pytest.fixture(scope="session")
def migrated_db() -> dict[str, str]:
    """Reset schema (admin), migrate (admin), bootstrap the app role + grants, then seed as
    the app role. Returns the seeded ids."""
    if not _admin_reachable():
        pytest.skip("no Postgres reachable for RLS/leakage tests")

    from alembic import command
    from alembic.config import Config
    from sqlalchemy import create_engine

    admin_eng = create_engine(_ADMIN_DSN)
    with admin_eng.begin() as conn:
        conn.execute(text("DROP SCHEMA public CASCADE"))
        conn.execute(text("CREATE SCHEMA public"))

    cfg = Config("alembic.ini")
    command.upgrade(cfg, "head")

    _bootstrap_app_role()

    # Reset the cached app engine so it picks up the (now-existing) vigil_app role.
    from vigil.db import engine as engine_mod

    engine_mod.get_engine.cache_clear()
    engine_mod.get_session_factory.cache_clear()

    from vigil.seed import seed

    return seed()
