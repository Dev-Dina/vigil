"""Secret sourcing — Vault is the single source of truth (infra.md).

Every secret (JWT signing key, DB credentials, LLM keys) is read here, never hardcoded
and never read directly from process env by application code. Two backends:

- ``VaultSecrets`` — talks to a Vault server (dev or prod) over the KV v2 engine. This is
  the path used in Docker Compose / k8s where ``vault`` runs as a service.
- ``EnvSecrets`` — a local-dev / CI shim that reads the same logical secret names from the
  environment. It exists so the spine is runnable and the leakage test can stand up a
  Postgres without a running Vault. It is deliberately explicit: it fails LOUD if a
  required secret is absent (no silent default).

The active backend is chosen by ``VIGIL_SECRETS_BACKEND`` (``vault`` | ``env``). The rest of
the codebase depends only on the :class:`SecretsProvider` protocol, so swapping backends is
a one-line config change and no caller can accidentally hardcode a secret.
"""

from __future__ import annotations

import os
from typing import Protocol, runtime_checkable

# Logical secret paths. Application code refers to secrets by these names ONLY.
JWT_SIGNING_KEY = "vigil/auth/jwt_signing_key"
DB_DSN = "vigil/db/dsn"
LLM_API_KEY = "vigil/llm/api_key"  # OpenRouter (fallback provider)
ANTHROPIC_API_KEY = "vigil/llm/anthropic_api_key"  # Anthropic (primary provider)
LANGFUSE_PUBLIC_KEY = "vigil/langfuse/public_key"  # Langfuse tracing (Phase 6.3)
LANGFUSE_SECRET_KEY = "vigil/langfuse/secret_key"  # Langfuse tracing (Phase 6.3)
NOTIFY_EMAIL_PASSWORD = "vigil/notifications/email_password"  # Gmail App Password (Phase 9.6 SMTP)


class SecretNotFoundError(RuntimeError):
    """Raised when a required secret is missing — never swallowed, never defaulted."""


@runtime_checkable
class SecretsProvider(Protocol):
    """The only contract the app depends on for reading secrets."""

    def get(self, name: str) -> str: ...


class EnvSecrets:
    """Local-dev / CI backend: maps logical secret names to environment variables.

    The mapping is fixed and explicit; an unmapped or unset name fails loud.
    """

    _ENV_MAP: dict[str, str] = {
        JWT_SIGNING_KEY: "VIGIL_JWT_SIGNING_KEY",
        DB_DSN: "VIGIL_DB_DSN",
        LLM_API_KEY: "VIGIL_LLM_API_KEY",
        ANTHROPIC_API_KEY: "VIGIL_ANTHROPIC_API_KEY",
        LANGFUSE_PUBLIC_KEY: "VIGIL_LANGFUSE_PUBLIC_KEY",
        LANGFUSE_SECRET_KEY: "VIGIL_LANGFUSE_SECRET_KEY",
        NOTIFY_EMAIL_PASSWORD: "VIGIL_NOTIFY_EMAIL_PASSWORD",
    }

    def get(self, name: str) -> str:
        env_var = self._ENV_MAP.get(name)
        if env_var is None:
            raise SecretNotFoundError(f"no env mapping for secret {name!r}")
        value = os.environ.get(env_var)
        if value is None or value == "":
            raise SecretNotFoundError(
                f"secret {name!r} not set (expected env var {env_var})"
            )
        return value


class VaultSecrets:
    """Vault KV v2 backend. Reads each logical name as a ``value`` field at that path."""

    def __init__(self, addr: str, token: str, mount: str = "secret") -> None:
        import hvac

        self._client = hvac.Client(url=addr, token=token)
        if not self._client.is_authenticated():
            raise SecretNotFoundError("Vault authentication failed")
        self._mount = mount

    def get(self, name: str) -> str:
        from hvac.exceptions import InvalidPath

        try:
            resp = self._client.secrets.kv.v2.read_secret_version(
                path=name, mount_point=self._mount, raise_on_deleted_version=True
            )
        except InvalidPath as exc:  # noqa: F841
            raise SecretNotFoundError(f"secret {name!r} not present in Vault") from exc
        data = resp["data"]["data"]
        if "value" not in data:
            raise SecretNotFoundError(f"secret {name!r} has no 'value' field")
        return str(data["value"])


def build_secrets_provider() -> SecretsProvider:
    """Choose the backend from ``VIGIL_SECRETS_BACKEND`` (default: ``env`` for local dev).

    Vault is the production source of truth; the env backend is the documented local-dev
    shim from infra.md. Either way, secrets are never inlined in code.
    """

    backend = os.environ.get("VIGIL_SECRETS_BACKEND", "env").lower()
    if backend == "env":
        return EnvSecrets()
    if backend == "vault":
        addr = os.environ.get("VAULT_ADDR")
        token = os.environ.get("VAULT_TOKEN")
        if not addr or not token:
            raise SecretNotFoundError(
                "VAULT_ADDR and VAULT_TOKEN must be set for the vault backend"
            )
        mount = os.environ.get("VAULT_KV_MOUNT", "secret")
        return VaultSecrets(addr=addr, token=token, mount=mount)
    raise SecretNotFoundError(f"unknown secrets backend {backend!r}")
