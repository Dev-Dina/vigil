"""Minimal config for the isolated public Guide (specs/isolation.md § Phase 7 ratified decisions).

The Guide's ENTIRE allowed config/secret set is exactly three things: its own approved-docs index
path, its own ``message_events`` sink DSN, and its own LLM key. There is deliberately NO field for
a participant-DB DSN, a broad Vault token, a Redis URL, the queue URL, or any internal/admin URL —
the deny-listed credentials are absent BY CONSTRUCTION, asserted by the config/secret audit
(`tests/guide/test_config_audit.py`). This module imports nothing from `vigil.*`.
"""

from __future__ import annotations

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class GuideConfig(BaseSettings):
    """Allowed-creds-only settings for the Guide. Env prefix ``VIGIL_GUIDE_``."""

    model_config = SettingsConfigDict(
        env_prefix="VIGIL_GUIDE_", env_file=".env.guide", extra="ignore"
    )

    # --- non-secret runtime ---
    host: str = "0.0.0.0"
    port: int = 8080
    log_level: str = "INFO"

    # (b) the Guide's OWN read-only approved-docs index — file-backed, built in Gate 7.2. UNUSED
    #     here; no credential (a local path).
    approved_docs_index_path: str = "guide/data/approved_index"

    # (a) the Guide's OWN message_events sink — its OWN store, NEVER the app Postgres. SQLite by
    #     default so the Guide shares no datastore; a Guide-owned Postgres is a Phase-8 swap.
    message_events_sink_dsn: str = "sqlite+pysqlite:///guide_message_events.db"

    # (c) the Guide's OWN LLM key — Vault path secret/vigil/guide/llm_api_key (env shim
    #     VIGIL_GUIDE_LLM_API_KEY). Distinct from the app's vigil/llm/* keys. UNUSED in 7.1.
    llm_api_key: str = ""


@lru_cache(maxsize=1)
def get_config() -> GuideConfig:
    """Return the Guide's config singleton."""
    return GuideConfig()
