# Local-dev Vault — PERSISTENT file storage (NOT dev-mode/inmem).
#
# Contains NO secrets and NO unseal material — safe to commit. Secrets are written into Vault
# at runtime (see the dev runbook in README.md); the encrypted data lives in the `vault-data`
# Docker volume, never in the repo.
#
# Unlike `-dev` mode this Vault starts UNINITIALISED + SEALED: it must be `operator init`'d once
# and `operator unseal`'d on every start (dev lifecycle in README.md). There is no fixed
# `vigil-dev-root` token anymore — the root token comes from `operator init` and stays in your
# terminal/password manager only.
#
# Phase 8 (k8s) swaps this for: storage "raft" (HA, PVC-backed StatefulSet), tls_disable = 0
# (TLS on), KMS/transit AUTO-UNSEAL, and a Kubernetes-auth AppRole app token instead of root.

storage "file" {
  path = "/vault/file"
}

listener "tcp" {
  address     = "0.0.0.0:8200"
  tls_disable = 1
}

api_addr      = "http://127.0.0.1:8200"
disable_mlock = true # dev convenience; avoids the mlock / IPC_LOCK requirement
ui            = true
