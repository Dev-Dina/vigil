"""Gate 7.1 — config/secret audit (specs/isolation.md §1).

The Guide's config exposes ONLY its allowed set — its own approved-docs index path, its own
message_events sink DSN, its own LLM key — and has NO field for any deny-listed credential
(participant-DB DSN, broad Vault token, Redis, queue, internal/admin URL). The deny-listed creds
are absent by construction.
"""

from __future__ import annotations

from guide.config import GuideConfig, get_config

# The Guide's ENTIRE allowed config/secret surface (specs/isolation.md § Phase 7 ratified).
_ALLOWED_FIELDS = {
    # runtime
    "host",
    "port",
    "log_level",
    # (b) own file-backed approved-doc index
    "approved_docs_path",
    "approved_docs_index_path",
    # (a) own message_events sink
    "message_events_sink_dsn",
    # (c) own LLM key + the Guide's OWN minimal LLM client config
    "llm_api_key",
    "llm_stub",
    "llm_provider",  # non-secret: which client to build ("openai_compatible" | "anthropic")
    "llm_base_url",
    "llm_model",
    "llm_timeout_seconds",
    "llm_max_tokens",
    # own offline embedder + retrieval/sound-refusal tuning
    "embedding_model_path",
    "embedding_dim",
    "retrieval_top_k",
    "relevance_threshold",
}

# Substrings that would betray a deny-listed CREDENTIAL or internal/admin ROUTE on a field name.
# NB: "token" is deliberately NOT here — it would false-positive on the legitimate LLM token
# budget (`llm_max_tokens`); a broad VAULT token is caught by "vault".
_FORBIDDEN_SUBSTRINGS = (
    "vault",
    "redis",
    "queue",
    "arq",
    "jwt",
    "signing",
    "participant",
    "admin",
    "postgres",
    "scope",
    "session",
)


def test_config_exposes_only_the_allowed_set() -> None:
    fields = set(GuideConfig.model_fields)
    assert fields == _ALLOWED_FIELDS, (
        f"unexpected config surface: {fields ^ _ALLOWED_FIELDS}"
    )


def test_no_denylisted_credential_fields() -> None:
    for name in GuideConfig.model_fields:
        low = name.lower()
        for bad in _FORBIDDEN_SUBSTRINGS:
            assert bad not in low, (
                f"deny-listed cred-ish field on Guide config: {name!r}"
            )


def test_only_dsn_is_the_own_sink() -> None:
    # The ONLY DSN the Guide may hold is its own message_events sink — never an app/participant DB.
    dsn_fields = [n for n in GuideConfig.model_fields if "dsn" in n.lower()]
    assert dsn_fields == ["message_events_sink_dsn"]


def test_default_sink_is_guide_owned_not_app_postgres() -> None:
    cfg = get_config()
    dsn = cfg.message_events_sink_dsn.lower()
    assert dsn.startswith("sqlite"), (
        "default Guide sink must be its own store, not app Postgres"
    )
    assert "5432" not in dsn and "postgres" not in dsn
