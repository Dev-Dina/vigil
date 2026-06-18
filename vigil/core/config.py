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

    # --- demo / scoring ---
    # set via VIGIL_DEMO_MODE=true; in production read from Vault
    demo_mode: bool = False
    # validated against the demo_mode_key in POST /scoring/inject_events
    demo_secret: str = "vigil-demo-secret"

    # --- llm / agent layer (Phase 5) ---
    # Anthropic PRIMARY (claude-haiku-4-5) + OpenRouter FALLBACK on transient error (specs/rag.md
    # § Decisions). Egress allow-listed to api.anthropic.com + openrouter.ai ONLY (first outbound
    # LLM egress; deny-by-default). CI is hermetic: VIGIL_LLM_STUB=true selects the fake client
    # (no network, no key) BEFORE any real client is built — covers BOTH providers.
    llm_stub: bool = False
    llm_timeout_seconds: float = 30.0
    llm_max_tokens: int = 1024

    # Anthropic (PRIMARY). anthropic_enabled defaults true (it's the primary now); if enabled but
    # its key is missing the factory fails loud (no silent downgrade to OpenRouter-only).
    anthropic_enabled: bool = True
    anthropic_base_url: str = "https://api.anthropic.com"
    anthropic_model: str = "claude-haiku-4-5"
    anthropic_cost_per_1k_tokens: float = 0.0

    # OpenRouter (FALLBACK). Kept as the llm_* names (no rename); free model, cost ~0.
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_model: str = "meta-llama/llama-3.3-70b-instruct:free"
    # USD per 1k (prompt+completion) tokens for the cost estimate; 0.0 for a free model.
    llm_cost_per_1k_tokens: float = 0.0

    # --- langfuse tracing (Phase 6.3) ---
    # Per-turn trace ON TOP of the durable message_events record. CI-hermetic: langfuse_enabled
    # defaults FALSE, so CI/tests make NO Langfuse call and need NO key (same posture as
    # VIGIL_LLM_STUB). Enabled only on local/demo with real Vault keys. The host is a NEW outbound
    # egress — allow-listed (deny-by-default, /specs/isolation.md) alongside the LLM providers.
    # Tracing is additive/best-effort: a disabled or failing Langfuse NEVER breaks a turn or its
    # mandatory message_events write. Keys come from Vault (vigil/langfuse/*), never inlined.
    langfuse_enabled: bool = False
    langfuse_host: str = "https://cloud.langfuse.com"

    # --- notification email / SMTP (Phase 9.6) ---
    # PII-free serious-risk "doorbell" email, sent from an Arq job (never inline). CI-hermetic:
    # email_stub defaults TRUE → the StubEmailSender sends NOTHING and needs NO credential (safest
    # posture; mirrors VIGIL_LLM_STUB/VIGIL_LANGFUSE_ENABLED). The live send is an explicit opt-in
    # (set VIGIL_EMAIL_STUB=false + the Gmail App Password in Vault). The SMTP host joins the
    # app-side deny-by-default egress allow-list (specs/isolation.md, specs/infra.md) alongside
    # api.anthropic.com / openrouter.ai / the Langfuse host. Host/port/sender are non-secret config;
    # the App Password is the ONLY new secret (Vault vigil/notifications/email_password).
    email_stub: bool = True
    smtp_host: str = "smtp.gmail.com"
    smtp_port: int = 587  # Gmail SMTP submission (STARTTLS)
    notify_from_address: str = (
        ""  # the From/login account (non-secret); required for a live send
    )
    # Base URL of the authenticated app for the at-risk deep link in the email body (no PII).
    app_base_url: str = "http://localhost:3000"

    # --- embeddings (Phase 5) — LOCAL, vendored, OFFLINE (no embedding API, no runtime fetch) ---
    # The single real embedder is sentence-transformers all-MiniLM-L6-v2 (dim 384), loaded from
    # the VENDORED weights in-repo. Relative path binds to the repo root. No lexical fallback.
    embedding_model_path: str = "data/models/embeddings/all-MiniLM-L6-v2"
    embedding_dim: int = 384

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
        """OpenRouter (fallback) key."""
        return self._secrets.get(secret_paths.LLM_API_KEY)

    @cached_property
    def anthropic_api_key(self) -> str:
        """Anthropic (primary) key. Read only when the Anthropic client is built."""
        return self._secrets.get(secret_paths.ANTHROPIC_API_KEY)

    @cached_property
    def langfuse_public_key(self) -> str:
        """Langfuse public key. Read only when tracing is enabled + the tracer is built."""
        return self._secrets.get(secret_paths.LANGFUSE_PUBLIC_KEY)

    @cached_property
    def langfuse_secret_key(self) -> str:
        """Langfuse secret key. Read only when tracing is enabled + the tracer is built."""
        return self._secrets.get(secret_paths.LANGFUSE_SECRET_KEY)

    @cached_property
    def notify_email_password(self) -> str:
        """Gmail App Password for the 9.6 SMTP notifier. Read ONLY when the real SMTP sender is
        built (email_stub=False); the stub sender never reads it (CI needs no credential)."""
        return self._secrets.get(secret_paths.NOTIFY_EMAIL_PASSWORD)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Return the process-wide settings singleton."""
    return Settings()
