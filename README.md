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

## Local dev — Vault (production-shaped, self-hosted), unseal lifecycle

`docker-compose.dev.yml` runs Vault **production-shaped for a self-hosted (no-cloud) deployment**:
**non-root** (the `vault` user, uid 100), **persistent file storage** (config: `infra/vault/vault.hcl`,
data: the `vault-data` Docker volume), and **Shamir-sealed** (multi-key, 3-of-5). Unlike `-dev` mode it
is **not** auto-unsealed and has **no fixed `vigil-dev-root` token** — it starts **uninitialised +
sealed**, secrets survive restarts, and you unseal on each start with a quorum of key shares.

This is the real self-hosted posture. A **cloud** deployment (Phase 8) swaps the **manual Shamir unseal
for KMS/transit AUTO-UNSEAL** (and a Kubernetes-auth AppRole app token instead of root) — that is a
**config change, not an architecture change**; non-root + persistent storage + sealed-at-rest are
identical. We do **not** run cloud auto-unseal here (no KMS available).

> Secrets and unseal material live ONLY in your terminal / password manager. Never paste an unseal
> key or the root token into a committed file, the compose file, or `vault.hcl`. `.gitignore` already
> excludes any local init-output / bind-mounted data.

**(a) Bring up the persistent Vault** (+ Postgres/Redis):
```
make db-up            # docker compose -f docker-compose.dev.yml up -d postgres redis
docker compose -f docker-compose.dev.yml up -d vault
```

**Volume permissions (first time / fresh volume):** Vault runs as uid 100, so the `vault-data` volume
must be owned by it. A brand-new named volume is created root-owned — chown it ONCE (the container will
otherwise fail to write the file store). The compose-qualified volume name is `vigil_vault-data`:
```
docker run --rm -v vigil_vault-data:/vault/file --entrypoint chown hashicorp/vault:1.17 -R 100:1000 /vault/file
```

**(b) First time only — initialise** with real Shamir key-splitting (**3-of-5**):
```
docker exec -e VAULT_ADDR=http://127.0.0.1:8200 vigil-vault-1 \
  vault operator init -key-shares=5 -key-threshold=3
```
SAVE OFF-REPO (password manager / split across custodians): **all 5 `Unseal Key` shares** and the
**`Initial Root Token`**. Unsealing requires **any 3 of the 5** shares. **Losing 3+ shares (or the only
copies) = permanent, unrecoverable data loss** — the file store is encrypted by the master key the
shares reconstruct; there is no backdoor. Re-running `init` is only possible after wiping the
`vault-data` volume (then re-init + re-seed).

**(c) Unseal — on first init AND after every restart** (Vault re-seals whenever the container stops).
Run `operator unseal` **three times with three DIFFERENT shares** (3-of-5 threshold); each call advances
`Unseal Progress` until the third flips `Sealed` to `false`:
```
docker exec -e VAULT_ADDR=http://127.0.0.1:8200 vigil-vault-1 vault operator unseal <UNSEAL_KEY_1>
docker exec -e VAULT_ADDR=http://127.0.0.1:8200 vigil-vault-1 vault operator unseal <UNSEAL_KEY_2>
docker exec -e VAULT_ADDR=http://127.0.0.1:8200 vigil-vault-1 vault operator unseal <UNSEAL_KEY_3>
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

**Day-to-day after a restart:** just re-run **(c) unseal** with any 3 of your saved shares, then **(e)**.
Re-seeding is NOT needed — the secrets persist in `vault-data`.

## Phase 0 loop
1. Co-author the specs (fill the `TODO:` markers); `isolation.md` and `data.md` first.
2. Keep `make check-specs` green.
3. The subagents and skills are committed, so they are versioned and shared.

Optional: `.claude/settings.json` can pin a default model or permissions — left out here so
nothing is asserted that you have not chosen. Add it when you want it.
