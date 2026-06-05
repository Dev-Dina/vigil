#!/usr/bin/env bash
# Seed the local Vault dev server with the spine's secrets (KV v2 at mount "secret").
# Logical paths match vigil/core/secrets.py. Run AFTER `make db-up`.
#
#   VAULT_ADDR=http://localhost:8200 VAULT_TOKEN=vigil-dev-root ./scripts/vault_dev_seed.sh
#
# Then run the app with: VIGIL_SECRETS_BACKEND=vault VAULT_ADDR=... VAULT_TOKEN=...
set -euo pipefail

: "${VAULT_ADDR:=http://localhost:8200}"
: "${VAULT_TOKEN:=vigil-dev-root}"
export VAULT_ADDR VAULT_TOKEN

# A 32+ byte signing key for HS256; rotate by writing a new version.
vault kv put secret/vigil/auth/jwt_signing_key value="$(openssl rand -hex 32)"
vault kv put secret/vigil/db/dsn value="postgresql+psycopg://vigil_app:vigil_app_pw@localhost:55432/vigil"
vault kv put secret/vigil/llm/api_key value="dev-llm-key-placeholder"

echo "Vault dev seeded: jwt_signing_key, db/dsn, llm/api_key"
