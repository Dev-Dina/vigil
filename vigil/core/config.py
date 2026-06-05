"""One config object for the whole app.

Non-secret config (hosts, ports, log level, token TTLs) comes from the environment via
pydantic-settings. **Secrets** (JWT signing key, DB DSN, LLM key) come from
:mod:`vigil.core.secrets` (Vault, or the local-dev env shim) and are exposed here as lazy,
cached properties — so there is exactly one place to ask for any config value and secrets
are never read ad hoc around the codebase.
"""

from __future__ import annotations

from functools import cached_property, lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict

from vigil.core import secrets as secret_paths
from vigil.core.secrets import SecretsProvider, build_secrets_provider


class Settings(BaseSettings):
    """Process-wide configuration. Non-secret values only; secrets are resolved lazily."""

    model_config = SettingsConfigDict(
        env_prefix="VIGIL_", env_file=".env", extra="ignore"
    )

    # --- runtime ---
    env: str = "local"
    log_level: str = "INFO"

    # --- redis (sessions, cache, rate limit, arq queue) ---
    redis_url: str = "redis://localhost:6379/0"

    # --- auth / token policy (non-secret) ---
    jwt_issuer: str = "vigil-auth"
    jwt_algorithm: str = "HS256"
    access_token_ttl_seconds: int = 1800  # <= 30 min per api.md
    refresh_token_ttl_seconds: int = 60 * 60 * 24 * 14  # 14 days
    session_ttl_seconds: int = 60 * 60 * 24 * 14

    # --- rate limiting ---
    rate_limit_per_user_per_minute: int = 120
    rate_limit_per_tenant_per_minute: int = 1200

    # --- db pool ---
    db_pool_size: int = 5
    db_max_overflow: int = 10

    @cached_property
    def _secrets(self) -> SecretsProvider:
        return build_secrets_provider()

    @cached_property
    def jwt_signing_key(self) -> str:
        return self._secrets.get(secret_paths.JWT_SIGNING_KEY)

    @cached_property
    def db_dsn(self) -> str:
        """SQLAlchemy DSN for the app. Sourced from Vault/env — never hardcoded."""
        return self._secrets.get(secret_paths.DB_DSN)

    @cached_property
    def llm_api_key(self) -> str:
        return self._secrets.get(secret_paths.LLM_API_KEY)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
