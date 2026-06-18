# Vigil — Local Bring-up & Live-Verification Runbook

A precise, ordered guide to stand Vigil up locally end-to-end and verify the **live** paths that
CI never exercises (CI is hermetic by design: stub LLM, stub email, Langfuse off). Follow it top to
bottom. Commands assume a POSIX-ish shell from the repo root; on Windows use Git Bash or adapt to
PowerShell.

> **Honesty frame (read first).** The deep-learning sequence signal is a **labeled-synthetic
> capability demonstration with a planted precursor rule — NOT a clinical finding.** Present it as
> architecture/method validity only. See `data/models/PHASE3_CARD.md`.

---

## 0. Prerequisites

| Tool | Purpose |
|---|---|
| Docker + Docker Compose | Postgres, Redis, Vault (the dev stack) |
| `uv` | Python env + runner (`uv run …`) |
| Node 18+ / npm | the Next.js frontend |
| `kubectl` + `kind` + a container runtime | the layer-3 Guide isolation proof (Calico NetworkPolicies) |
| `vault` CLI (optional) | convenience for writing secrets; you can also `docker compose exec vault …` |

The repo's dev services are defined in `docker-compose.dev.yml` (postgres, redis, vault).

---

## 1. Service bring-up (ordered)

> **Two ways to run the app.** Section **1.A** is the **Dockerized app stack** (Gate D1) — the
> quickest dev path: API + worker run as containers against an **auto-unsealed dev Vault**, no
> manual unseal. Sections **1.1–1.5** are the **host/uv flow** against the **prod-shaped,
> manually-unsealed Vault** — the model that mirrors production. Pick one. The security model is
> identical in both: the app reads secrets from Vault and connects to Postgres as the non-superuser
> `vigil_app` under RLS.

### 1.A Dockerized app stack — DEV ONLY (Gate D1, quickest path)
The API + worker share one image (`./Dockerfile`); they read secrets from a **dev-mode Vault**
(`vault-dev`, auto-unsealed, fixed root token `vigil-dev-root`, **in-memory — dev only**) and
reach Postgres/Redis **by service name** on the compose network. The dev Vault is seeded on
bring-up with the **in-container** DB DSN (`…@postgres:5432/vigil`, resolving the host
`localhost:55432` → service-name `postgres:5432` difference inside the secret value).

```bash
# one command: builds the image, starts postgres + redis + vault-dev + vault-seed + api + worker.
docker compose -f docker-compose.dev.yml --profile app up -d --build
```
`depends_on` ordering guarantees the app waits for **Postgres healthy + the dev Vault seeded**
before starting (no manual sequencing). The API is on **localhost:8000**.

First-time / fresh-volume DB only (the existing dev volume already has these) — create the
`vigil_app` role + grants once, then migrate (as the owner) + seed (as `vigil_app`):
```bash
docker compose -f docker-compose.dev.yml exec -T postgres \
  psql -U vigil -d vigil -v app_password=vigil_app_pw -f - < scripts/bootstrap_db.sql  # role+grants
docker compose -f docker-compose.dev.yml --profile tools run --rm migrate              # alembic (owner)
docker compose -f docker-compose.dev.yml --profile tools run --rm seed                 # vigil.seed (vigil_app)
```
Verify: `curl localhost:8000/healthz` → `{"status":"ok"}`; then log in (`POST /api/v1/auth/login`,
e.g. `coord.a@vigil.example` / `vigil-dev-password`) and `GET /api/v1/cohort` with the token — the
coordinator sees only their own site (RLS + SEC-1 intact, served by the container as `vigil_app`).

> **DEV vs PROD Vault.** `vault-dev` is **dev-only** (in-memory, auto-unsealed, fixed token). The
> **production model is the persistent, Shamir-sealed `vault` service** (profile `prod-vault`,
> `infra/vault/vault.hcl`) with **manual `operator init`/`operator unseal`** — exactly the host
> flow in **1.1–1.5** below. Phase 8 (k8s) replaces it with raft HA + KMS/transit auto-unseal.
> `frontend` (D2) + `guide` (D3, profile `guide`) are **not** in the app stack yet.

### 1.1 Start the dev stack (host/uv flow — prod-shaped Vault)
```bash
docker compose -f docker-compose.dev.yml up -d postgres redis
docker compose -f docker-compose.dev.yml --profile prod-vault up -d vault   # prod-shaped Vault
```
Postgres listens on **localhost:55432**, Redis on **localhost:6379**, Vault on **localhost:8200**.

### 1.2 Unseal Vault
Vault starts **UNINITIALISED + SEALED**. On first ever start, initialise once (5 key shares,
threshold 3); on every subsequent start, just unseal with **3 of the 5** keys.

```bash
# FIRST TIME ONLY — initialise (record the 5 unseal keys + root token SECURELY, off-repo):
docker compose -f docker-compose.dev.yml exec vault vault operator init

# EVERY START — unseal with 3 of 5 keys:
docker compose -f docker-compose.dev.yml exec vault vault operator unseal <key-1>
docker compose -f docker-compose.dev.yml exec vault vault operator unseal <key-2>
docker compose -f docker-compose.dev.yml exec vault vault operator unseal <key-3>
```

> **The unseal keys + root token are NOT in the repo.** They live in the operator's password
> manager. `infra/vault/data/`, `infra/vault/init.json`, and `infra/vault/*.local` are git-ignored
> precisely so unseal material is never committed.

### 1.3 Environment for the app processes
Point the app at Vault (the production source of truth):
```bash
export VIGIL_SECRETS_BACKEND=vault
export VAULT_ADDR=http://localhost:8200
export VAULT_TOKEN=<root-or-app-token>     # from `vault operator init`
# export VAULT_KV_MOUNT=secret             # default; only if you mounted KV elsewhere
```
For a **hermetic, no-Vault** local run instead, use the env shim: `VIGIL_SECRETS_BACKEND=env` and
set each `VIGIL_*` var from §3 directly (this is what the test suite does — see
`tests/spine/conftest.py`).

### 1.4 Migrate + seed
```bash
uv run alembic upgrade head                # apply the RLS schema (Makefile: `make migrate`)

# Seed the two-sponsor isolation fixture (Makefile: `make seed`).
# Set VIGIL_DEMO_NOTIFY_EMAIL so the Phase-9 demo coordinator (coord.a) has a real inbox for the
# live email path; omit it and coord.a is seeded WITHOUT a notification address.
export VIGIL_DEMO_NOTIFY_EMAIL=you@example.com
uv run python -m vigil.seed
```

### 1.5 Start the processes
```bash
# API (FastAPI) on :8000
uv run uvicorn vigil.api.app:app --reload --port 8000      # Makefile: `make api`

# Arq worker (scoring, notifications) — separate terminal
uv run arq vigil.workers.settings.WorkerSettings           # Makefile: `make worker`

# Frontend (Next.js) on :3000 — separate terminal
cd frontend && npm install && npm run dev

# Public Guide service (isolated; its OWN key + index) on :8080 — separate terminal
uv run python -m guide.build_index           # build the approved-docs index first (once)
uv run uvicorn guide.app:app --port 8080
```

---

## 2. The Vault secret paths (exact — verified against `vigil/core/secrets.py`)

KV **v2**, mount `secret`, every secret stored under the field **`value`**. The logical name below
is what the app code requests; the CLI path is `secret/<logical-name>`. Each has an **env-shim**
var (used when `VIGIL_SECRETS_BACKEND=env`).

| Secret (logical name) | CLI path | Field | Env-shim var | Used by |
|---|---|---|---|---|
| `vigil/auth/jwt_signing_key` | `secret/vigil/auth/jwt_signing_key` | `value` | `VIGIL_JWT_SIGNING_KEY` | JWT signing/verify |
| `vigil/db/dsn` | `secret/vigil/db/dsn` | `value` | `VIGIL_DB_DSN` | app DB connection (RLS role) |
| `vigil/llm/api_key` | `secret/vigil/llm/api_key` | `value` | `VIGIL_LLM_API_KEY` | OpenRouter (fallback LLM) |
| `vigil/llm/anthropic_api_key` | `secret/vigil/llm/anthropic_api_key` | `value` | `VIGIL_ANTHROPIC_API_KEY` | **Anthropic (primary LLM)** |
| `vigil/langfuse/public_key` | `secret/vigil/langfuse/public_key` | `value` | `VIGIL_LANGFUSE_PUBLIC_KEY` | Langfuse tracing |
| `vigil/langfuse/secret_key` | `secret/vigil/langfuse/secret_key` | `value` | `VIGIL_LANGFUSE_SECRET_KEY` | Langfuse tracing |
| `vigil/notifications/email_password` | `secret/vigil/notifications/email_password` | `value` | `VIGIL_NOTIFY_EMAIL_PASSWORD` | Phase-9 Gmail SMTP App Password |

**Guide service — its OWN key, isolated from the app** (verified against `guide/config.py` +
`guide/llm.py`; the Guide imports nothing from `vigil.*`):

| Secret | CLI path | Field | Env-shim var |
|---|---|---|---|
| Guide LLM key | `secret/vigil/guide/llm_api_key` | `value` | `VIGIL_GUIDE_LLM_API_KEY` (env prefix `VIGIL_GUIDE_`) |

Example write:
```bash
vault kv put secret/vigil/llm/anthropic_api_key value=sk-ant-...
vault kv put secret/vigil/guide/llm_api_key      value=sk-or-...
vault kv put secret/vigil/notifications/email_password value="<gmail app password>"
```

---

## 3. The stub flags (defaults + which way to flip for a LIVE path)

Verified against `vigil/core/config.py` and `guide/config.py`. CI sets the first three to the
hermetic value in `conftest`; the **app defaults** are below.

| Flag | Default | What it does | For a LIVE path |
|---|---|---|---|
| `VIGIL_LLM_STUB` | `false` | `true` → `StubLLMClient` (deterministic, no network, no key) | leave **false** + place the Anthropic key |
| `VIGIL_LANGFUSE_ENABLED` | `false` | gates per-turn Langfuse tracing on top of the durable `message_events` | set **true** + place the Langfuse keys |
| `VIGIL_EMAIL_STUB` | `true` | `true` → `StubEmailSender` (records intent, no SMTP, no credential) | set **false** + place the SMTP App Password |
| `VIGIL_GUIDE_LLM_STUB` | `false` | `true` → deterministic Guide stub (no network, no key) | leave **false** + place the Guide key |

Other useful config: `VIGIL_DEMO_MODE` (default `false`; gates `POST /scoring/inject_events`),
`VIGIL_APP_BASE_URL` (default `http://localhost:3000`; the deep-link base in the Phase-9 email),
`VIGIL_LANGFUSE_HOST` (default `https://cloud.langfuse.com`).

---

## 4. Live-keys verification checklist (the run-once-live paths)

Each path below is **never exercised live in CI**. Run each once to confirm the real integration.

### (a) Live Anthropic LLM assistant turn
1. `vault kv put secret/vigil/llm/anthropic_api_key value=sk-ant-...`
2. Ensure `VIGIL_LLM_STUB` is unset/`false` (the live default).
3. Restart the API + worker so they pick up the key.
4. In the app (or via `POST /api/v1/assistant/conversations` then a message), ask an in-scope
   question and poll the job to completion.
5. **Success:** a real grounded answer returns; the persisted `message_events` row records
   `provider_model = anthropic/...` with a **non-zero** `latency_ms` / `token_cost_estimate`
   (a grounded/router refusal makes no generation call and stays honest-zero — that is correct).

### (b) Live Langfuse trace
1. `vault kv put secret/vigil/langfuse/public_key value=pk-lf-...` and `.../secret_key value=sk-lf-...`
2. `export VIGIL_LANGFUSE_ENABLED=true` (and `VIGIL_LANGFUSE_HOST` if self-hosted); restart.
3. Drive one assistant turn (as in (a)).
4. **Success:** a trace appears in the Langfuse project. **v4 caveat:** the tracer uses
   `client.create_event(...)` followed by an explicit `client.flush()` (`vigil/agents/tracing.py`)
   because the worker is short-lived — if no trace lands, confirm the flush ran and the host/keys
   are correct. The trace carries **redacted** content only (no raw PII), consistent with
   `message_events`.

### (c) Live Guide turn (isolated service)
1. `vault kv put secret/vigil/guide/llm_api_key value=sk-or-...` (or set `VIGIL_GUIDE_LLM_API_KEY`).
2. Ensure `VIGIL_GUIDE_LLM_STUB` is unset/`false`; (re)build the index `uv run python -m
   guide.build_index`; start `uvicorn guide.app:app --port 8080`.
3. Ask an **approved-docs** question against the Guide endpoint.
4. **Success:** a grounded answer cites approved-doc content; an out-of-scope / low-relevance
   question is refused. The Guide reaches ONLY its own approved-doc index — never the app DB,
   model endpoints, or app secrets (that is what (d) proves at the network layer).

### (d) Layer-3 kind + Calico isolation proof
1. From the repo root run the fixed proof script (keep the cluster up for inspection):
   ```powershell
   deploy/k8s/run-isolation-proof.ps1 -Keep
   ```
   (POSIX equivalent: `deploy/k8s/run-isolation-proof.sh`.)
2. **Success:** the transcript shows **reachable BEFORE the NetworkPolicy** → **DENIED AFTER** the
   deny-by-default policy is applied, plus a **positive control** (an allowed path still works) so
   the deny isn't a false "everything's broken". The run artifact is written to
   `deploy/k8s/last-proof-transcript.txt` (git-ignored).

### (e) Phase-9 live email doorbell
1. `vault kv put secret/vigil/notifications/email_password value="<gmail app password>"`
2. ```bash
   export VIGIL_EMAIL_STUB=false
   export VIGIL_NOTIFY_FROM_ADDRESS=<your-gmail-account>
   export VIGIL_DEMO_NOTIFY_EMAIL=<recipient-inbox>     # also re-seed so coord.a has this address
   export VIGIL_APP_BASE_URL=http://localhost:3000
   ```
3. Run the clinical-ops demo (drives a real crossing via the calibrated model):
   ```bash
   uv run python -m scripts.demo_clinical_ops_loop
   ```
4. **Success:** one **PII-free** email arrives at the recipient — body is the `/at-risk` deep link +
   the synthetic-demonstration label ONLY (no participant id / coded_ref / score / factor). It
   sends **once** per crossing (the `notified` flag dedupes re-fires).

---

## 5. The demo / defense flow (the honest presentation order)

Present the system in this order — each step states its own honesty boundary:

1. **Structural associations (breadth).** Pan-indication GBT, REAL registry data; lead with the
   HARD-1 confidence intervals. The pooled PR-AUC (0.697) largely reflects **between-indication
   base-rate variation**, not within-trial participant risk — say so.
2. **Per-indication honesty.** Show the within-indication decomposition: **T2D and MDD sit below
   their own within-indication chance line** (CIs entirely below 0.5). Do not over-read ALZ
   (directional, wide CI). This is the integrity centerpiece — `data/models/PHASE3_CARD.md`.
3. **Calibrated synthetic sequence demo.** The T2D LSTM (`sequence_v1.1:demo`, isotonic-calibrated)
   as a **capability / architecture proof on labeled-synthetic data with a planted precursor rule
   — NOT a clinical finding.** The +0.088 trajectory lift demonstrates the method, not real-world
   prediction.
4. **Agentic RAG.** The in-app assistant — grounded answers, router/guardrail refusals, citations
   to `message_events`.
5. **Observability.** `message_events` (redacted), the `/monitoring/*` cost/latency/model surfaces,
   and (live) Langfuse traces.
6. **The Guide — three-layer isolation proof.** Separate service + separate credentials +
   NetworkPolicies; run the layer-3 proof (§4d) to show the Guide can reach NO deny-listed
   resource.
7. **The clinical-ops loop (Phase 9).** The accruing-disengagement demo: an injected missed-visit
   sequence crosses `>0.6` via the REAL calibrated model → crossing → scope-bound at-risk surface
   with real reasons + recommended actions → the PII-free scope-bound email. Every surface carries
   the synthetic-demonstration label (`scripts/demo_clinical_ops_loop.py`).

---

## 6. Quick reference

| Service | URL | Start |
|---|---|---|
| API | http://localhost:8000 | `uv run uvicorn vigil.api.app:app --reload --port 8000` |
| Frontend | http://localhost:3000 | `cd frontend && npm run dev` |
| Guide | http://localhost:8080 | `uv run uvicorn guide.app:app --port 8080` |
| Postgres | localhost:55432 | `docker compose -f docker-compose.dev.yml up -d postgres` |
| Redis | localhost:6379 | `docker compose -f docker-compose.dev.yml up -d redis` |
| Vault (prod-shaped) | http://localhost:8200 | `docker compose -f docker-compose.dev.yml --profile prod-vault up -d vault` (+ unseal) |
| Dockerized app (api+worker+vault-dev) | API localhost:8000 | `docker compose -f docker-compose.dev.yml --profile app up -d --build` (Gate D1, dev-only auto-unseal) |

Make targets: `make db-up`, `make migrate`, `make seed`, `make api`, `make worker`,
`make check-specs`, `make leakage`, `make guide-isolation-proof`.
