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

    # (b) the Guide's OWN read-only approved-doc index — a FILE-BACKED index built by
    #     guide/build_index.py from guide/approved_docs/. NOT pgvector, NOT the app's
    #     document_chunk. A local path, no credential.
    approved_docs_path: str = "guide/approved_docs"
    approved_docs_index_path: str = "guide/data/approved_index.json"

    # (a) the Guide's OWN message_events sink — its OWN store, NEVER the app Postgres. SQLite by
    #     default so the Guide shares no datastore; a Guide-owned Postgres is a Phase-8 swap.
    message_events_sink_dsn: str = "sqlite+pysqlite:///guide_message_events.db"

    # (c) the Guide's OWN LLM key — Vault path secret/vigil/guide/llm_api_key (env shim
    #     VIGIL_GUIDE_LLM_API_KEY). Distinct from the app's vigil/llm/* keys. A NARROW key read,
    #     never a broad Vault token.
    llm_api_key: str = ""

    # --- LLM client (the Guide's OWN minimal client; NOT vigil.agents.llm) ---
    # Hermetic CI: VIGIL_GUIDE_LLM_STUB=true selects the deterministic stub (no network, no key).
    llm_stub: bool = False
    llm_base_url: str = "https://openrouter.ai/api/v1"
    llm_model: str = "meta-llama/llama-3.3-70b-instruct:free"
    llm_timeout_seconds: float = 30.0
    llm_max_tokens: int = 1024

    # --- embeddings (the Guide's OWN offline loader; NOT vigil.agents.embeddings) ---
    # A read-only, in-repo MiniLM weights dir the Guide MOUNTS (no credential, no network). The
    # deployment image bundles its own copy; this default points at the vendored weights for dev.
    embedding_model_path: str = "data/models/embeddings/all-MiniLM-L6-v2"
    embedding_dim: int = 384

    # --- retrieval / sound-refusal (specs/isolation.md § relevance-threshold refusal) ---
    # top-k chunks per query; a turn whose BEST cosine similarity is below the threshold REFUSES
    # (sound public refusal — not just zero-retrieval). Tuned to separate on-topic project
    # questions from off-topic/tangential ones over the approved-doc corpus.
    retrieval_top_k: int = 4
    relevance_threshold: float = 0.30


@lru_cache(maxsize=1)
def get_config() -> GuideConfig:
    """Return the Guide's config singleton."""
    return GuideConfig()
