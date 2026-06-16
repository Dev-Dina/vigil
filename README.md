# Vigil — repo scaffold (Phase 0)

Clinical-trial retention platform. This scaffold contains the Phase 0 foundation:
the project memory, the spec contracts, and the Claude Code tooling.

## Tooling
Python 3.12, managed with [uv](https://docs.astral.sh/uv/). `pyproject.toml` declares the
project (`vigil`) and the dev tools (`ruff`, `pytest`). `uv sync` creates `.venv/` and installs
them; run anything via `uv run ...` (e.g. `uv run python scripts/check_specs.py`).

## Layout
- `CLAUDE.md` — project memory, loaded every Claude Code session (principles, architecture, invariants).
- `pyproject.toml` — uv-managed project metadata and dev deps (`ruff`, `pytest`).
- `specs/` — the contracts (source of truth). Start with `isolation.md` and `data.md`.
- `.claude/skills/` — `data-cleaning`, `schema-migration`, `spec-conformance`.
- `.claude/agents/` — `ingestion`, `skeleton`, `public-demo`, `release`, `eda` (read-only
  analysis of the captured AACT snapshot) subagents.
- `.claude/commands/check-specs.md` — `/check-specs` slash command.
- `scripts/check_specs.py` — the conformance check. `make check-specs`.

## Ingestion golden set (real, committed)
The clean -> synthetic -> features pipeline runs offline against the **golden set**: a frozen
slice of **REAL PUBLIC ClinicalTrials.gov/AACT** trial-level data (snapshot `2026-06-05`)
committed alongside its expected cleaned `ref_*` output. It is the ingestion clean-transform
oracle (`assert_frame_equal` of `clean_snapshot(raw)` against `expected/`) and the non-live
pipeline substrate. **NO PHI, NO synthetic rows.** It lives at `tests/golden/` and is rebuilt
from the real snapshot on disk by:

```
make golden   # uv run python -m tests.golden.build_golden
```

The committed `tests/golden/raw/` + `expected/` + `selection.json` are what the fast suite and
CI use — no network, no committed `data/`, no fabricated fixture. Per `specs/data.md`
"Evaluation contract" the golden set is **solely** the ingestion transform oracle (golden =
transforms; models use held-out splits; RAG uses eval sets).

## Local dev — Vault (persistent), unseal lifecycle

`docker-compose.dev.yml` runs Vault with **persistent file storage** (config: `infra/vault/vault.hcl`,
data: the `vault-data` Docker volume). Unlike `-dev` mode this Vault is **not** auto-unsealed and has
**no fixed `vigil-dev-root` token** — it starts **uninitialised + sealed**, so secrets now survive
container restarts but you unseal on each start. **Dev only**; Phase 8 (k8s) replaces manual unseal
with **auto-unseal (KMS/transit)** and uses a **Kubernetes-auth AppRole app token** instead of root.

> Secrets and unseal material live ONLY in your terminal / password manager. Never paste the unseal
> key or root token into a committed file, the compose file, or `vault.hcl`. `.gitignore` already
> excludes any local init-output / bind-mounted data.

**(a) Bring up the persistent Vault** (+ Postgres/Redis):
```
make db-up            # docker compose -f docker-compose.dev.yml up -d postgres redis
docker compose -f docker-compose.dev.yml up -d vault
```

**(b) First time only — initialise** (1 unseal share for dev simplicity):
```
docker exec -e VAULT_ADDR=http://127.0.0.1:8200 vigil-vault-1 \
  vault operator init -key-shares=1 -key-threshold=1
```
SAVE OFF-REPO (password manager): the **`Unseal Key 1`** and the **`Initial Root Token`** from the
output. With `-key-shares=1` there is exactly one unseal key. **Losing either = permanent, unrecoverable
data loss** (the file store is encrypted by the unseal key; there is no backdoor). Re-running `init` is
only possible after wiping the `vault-data` volume (then you re-init + re-seed).

**(c) Unseal — on first init AND after every restart** (Vault re-seals when the container stops):
```
docker exec -e VAULT_ADDR=http://127.0.0.1:8200 vigil-vault-1 vault operator unseal <UNSEAL_KEY_1>
```

**(d) Seed the secrets — once after init** (they then persist; only re-unseal is needed later). Use the
**new root token** from step (b), not `vigil-dev-root`:
```
docker exec -e VAULT_ADDR=http://127.0.0.1:8200 -e VAULT_TOKEN=<ROOT_TOKEN> vigil-vault-1 \
  vault kv put secret/vigil/auth/jwt_signing_key value=<32-byte-hex>     # e.g. openssl rand -hex 32
docker exec -e VAULT_ADDR=http://127.0.0.1:8200 -e VAULT_TOKEN=<ROOT_TOKEN> vigil-vault-1 \
  vault kv put secret/vigil/db/dsn value="postgresql+psycopg://vigil_app:vigil_app_pw@localhost:55432/vigil"
docker exec -e VAULT_ADDR=http://127.0.0.1:8200 -e VAULT_TOKEN=<ROOT_TOKEN> vigil-vault-1 \
  vault kv put secret/vigil/llm/api_key value=<your-openrouter-key>
```
(`scripts/vault_dev_seed.sh` does the same three puts but defaults `VAULT_TOKEN` to the old
`vigil-dev-root` — override it with `<ROOT_TOKEN>` now that dev mode is gone.)

**(e) Run the app + worker against Vault** (PowerShell — set before starting each process):
```powershell
$env:VIGIL_SECRETS_BACKEND = "vault"
$env:VAULT_ADDR  = "http://127.0.0.1:8200"
$env:VAULT_TOKEN = "<ROOT_TOKEN>"
# clear env fallbacks so secrets demonstrably come from Vault:
Remove-Item Env:VIGIL_DB_DSN, Env:VIGIL_JWT_SIGNING_KEY, Env:VIGIL_LLM_API_KEY -ErrorAction SilentlyContinue

uv run uvicorn vigil.api.app:app --reload --port 8000   # API
# in a second terminal with the same 3 env vars:
uv run arq vigil.workers.settings.WorkerSettings         # worker
```

**Day-to-day after a restart:** just re-run **(c) unseal** with your saved key, then **(e)**. Re-seeding
is NOT needed — the secrets persist in `vault-data`.

## Phase 0 loop
1. Co-author the specs (fill the `TODO:` markers); `isolation.md` and `data.md` first.
2. Keep `make check-specs` green.
3. The subagents and skills are committed, so they are versioned and shared.

Optional: `.claude/settings.json` can pin a default model or permissions — left out here so
nothing is asserted that you have not chosen. Add it when you want it.
