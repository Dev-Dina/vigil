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
  psql -U vigil -d vigil -f - < scripts/bootstrap_db.sql  # role (vigil_app/vigil_app_pw) + grants; idempotent
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
> The **`frontend`** (D2) and the isolated **`guide`** (D3) are now first-class compose services
> under **profiles `frontend` / `guide`**; combine all three profiles for the one-command
> whole-system bring-up (**§1.D below**; the Guide stays on its own **`guide-net`**).

### 1.B Live-LLM bring-up against the PERSISTENT Vault — DEV ONLY, OPT-IN (Gate L1, real tokens)
The §1.A stack is **stubbed by default** (`VIGIL_LLM_STUB=true` → deterministic `StubLLMClient`, no
network, no key — the hermetic posture CI also uses). To run a **REAL Anthropic** (primary,
`claude-haiku-4-5`) call through the containerized stack, layer the **live override**
(`docker-compose.live.yml`): it repoints `api`+`worker` at the **persistent Vault** (the `vault`
service, file storage — not the in-memory `vault-dev`) and sets `VIGIL_LLM_STUB=false`. The Anthropic
key (and the rest of the app secret set) lives in the persistent Vault, **seeded once by hand** so it
persists across restarts — **no `.env`, no key in any committed file**.

> **Why the full secret set?** The app builds ONE Vault client from a single `VAULT_ADDR`/`VAULT_TOKEN`
> (`vigil/core/config.py`) — there is no per-secret Vault routing. Pointing `api`/`worker` at the
> persistent Vault means **that** Vault supplies *every* secret they read, so it must hold all four:
> `jwt_signing_key`, the **in-container** `db/dsn` (`…@postgres:5432/vigil`), the OpenRouter fallback
> `llm/api_key`, and `llm/anthropic_api_key`. (The host/uv flow's `db/dsn` is `localhost:55432` — a
> different value; for host runs use the env shim, §1.3.)

**Pre-conditions (you manage these):** the persistent Vault is **initialized + UNSEALED** (§1.2,
3-of-5 keys) and has **KV v2 at `secret`** (`vault secrets enable -version=2 -path=secret kv` once if
not). The override's healthcheck (`vault status`) gates `api`/`worker` on *unsealed* — a sealed Vault
cleanly blocks startup instead of crashing mid-request.

**Step 1 — seed the persistent Vault ONCE (root token; never committed).** Run via `docker exec`
into `vigil-vault-1` (KV v2, mount `secret`, field `value`):
```bash
ROOT=<persistent-vault-root-token>   # from `operator init`; in your password manager, never in the repo
docker exec vigil-vault-1 sh -c "VAULT_ADDR=http://127.0.0.1:8200 VAULT_TOKEN=$ROOT vault kv put secret/vigil/llm/anthropic_api_key value=sk-ant-..."
# the other three the containerized app reads (skip any already present with the CONTAINER db/dsn):
docker exec vigil-vault-1 sh -c "VAULT_ADDR=http://127.0.0.1:8200 VAULT_TOKEN=$ROOT vault kv put secret/vigil/auth/jwt_signing_key value=<signing-key>"
docker exec vigil-vault-1 sh -c "VAULT_ADDR=http://127.0.0.1:8200 VAULT_TOKEN=$ROOT vault kv put secret/vigil/db/dsn value=postgresql+psycopg://vigil_app:vigil_app_pw@postgres:5432/vigil"
docker exec vigil-vault-1 sh -c "VAULT_ADDR=http://127.0.0.1:8200 VAULT_TOKEN=$ROOT vault kv put secret/vigil/llm/api_key value=<openrouter-key-or-placeholder>"
```

**Step 2 — mint a scoped READ-ONLY token ONCE** (least-privilege; the app never gets the root token):
```bash
docker exec -i vigil-vault-1 sh -c "VAULT_ADDR=http://127.0.0.1:8200 VAULT_TOKEN=$ROOT vault policy write vigil-app-ro -" <<'HCL'
path "secret/data/vigil/*" { capabilities = ["read"] }
HCL
docker exec vigil-vault-1 sh -c "VAULT_ADDR=http://127.0.0.1:8200 VAULT_TOKEN=$ROOT vault token create -policy=vigil-app-ro -ttl=720h -field=token"
# → copy the printed token; it's what you export below. Re-mint when the TTL lapses.
```

**Step 3 — bring up the live stack (ONE command; the token comes from the shell, never committed):**
```bash
export VAULT_TOKEN=<scoped-read-token-from-step-2>
docker compose -f docker-compose.dev.yml -f docker-compose.live.yml --profile app up -d --build vault api worker
```
Naming `vault api worker` (+ their postgres/redis deps) starts exactly those; the override's
`depends_on` drops `vault-seed`, so `vault-dev`/`vault-seed` do **not** start. Make a turn
(`POST /api/v1/assistant/conversations` → a message → poll the job) and confirm the persisted
`message_events` row records `provider_model = anthropic/...` with a non-zero
`latency_ms`/`token_cost_estimate` (a grounded/router refusal makes no generation call and stays
honest-zero — correct). This **costs real tokens** and is opt-in only; the default
`docker compose -f docker-compose.dev.yml --profile app up` (no override) stays **stubbed** against
`vault-dev`, and CI/the spine never load the override. The Guide's live LLM is **separate** — it is
its own isolated service on `guide-net` with **no Vault route** and its **own** key; see §1.C.

### 1.C The isolated public Guide in Docker Compose (Gate D3)
The public **Guide** (the document-only chatbot behind `/welcome`) runs as a first-class compose
service under **profile `guide`**, on its **own `guide-net`** network with **no route** to the app's
`postgres`/`redis`/`vault`/`api`/`worker` (those are on the compose default network). It builds the
`guide/` package **alone** (imports nothing from `vigil.*`), serves **only** its file-backed
approved-doc index, writes to its **own** SQLite sink, and uses its **own** LLM key — never the app's
DB, Vault, or Anthropic key. The vendored MiniLM weights are **bind-mounted read-only** (host files,
not an app service) so the embedder works without weakening isolation.

**Stubbed by default (hermetic, no key) — whole-system, ONE command:**
```bash
docker compose -f docker-compose.dev.yml --profile app --profile guide up -d --build
```
This brings up infra + app + the Guide. The Guide builds its approved-doc index, then serves on
**:8080** (the frontend's `NEXT_PUBLIC_GUIDE_URL` default), `VIGIL_GUIDE_LLM_STUB=true` → a
deterministic stub answer, no network LLM call, no key needed.

Verify reachability + isolation:
```bash
curl localhost:8080/healthz                                  # {"status":"ok","service":"guide"}
curl -s localhost:8080/ask -H 'content-type: application/json' -d '{"question":"What is Vigil?"}'
# ISOLATION (must FAIL): the Guide cannot resolve/reach the app datastores on its separate network
docker compose -f docker-compose.dev.yml --profile guide exec guide \
  python -c "import socket; socket.create_connection(('postgres',5432),3)"   # -> getaddrinfo/refused
```

**LIVE Guide turn (key-safe, opt-in) — Anthropic-native, the Guide's OWN key, never committed:**
```bash
export VIGIL_GUIDE_LLM_API_KEY=sk-ant-...   # the Guide's OWN Anthropic key (NOT the app's key)
docker compose -f docker-compose.dev.yml -f docker-compose.live.yml --profile app --profile guide up -d --build
```
The live override (`docker-compose.live.yml`) flips `VIGIL_GUIDE_LLM_STUB=false` and selects the
**native Anthropic** client (`VIGIL_GUIDE_LLM_PROVIDER=anthropic`, `…_BASE_URL=https://api.anthropic.com`,
`…_MODEL=claude-haiku-4-5` — all overridable), injecting the key **from the shell** (`:?` fails loud if
unset). The Guide reads it from its own env — it has **no Vault client** — so there is no shared-secret
path to the app; its only new egress is `api.anthropic.com`. Default stays stubbed; CI/the spine are
unaffected (the Guide is not in the test path).

### 1.D Whole-system bring-up in ONE command (Gate D2 — UI included)
The Next.js frontend is containerized (`frontend/Dockerfile`, a multi-stage **production** build with
Next **standalone** output) and added to Compose under **profile `frontend`**. Combining the `app`,
`guide`, and `frontend` profiles brings up the **entire system** — infra + API + worker + isolated
Guide + UI — in one command:

```bash
# STUBBED (hermetic, no keys) — infra + app + worker + Guide + UI:
docker compose -f docker-compose.dev.yml --profile app --profile guide --profile frontend up -d --build
```
The **UI is then at http://localhost:3000**, the API at :8000, the Guide at :8080. (Fresh-volume DBs
still need the one-time role/migrate/seed from §1.A.)

> The stateless services (`frontend`/`guide`/`mailpit`) carry `restart: unless-stopped` so they
> survive a Docker Desktop / host restart on their own. The **persistent Vault always restarts
> SEALED** by design (Shamir keys off-repo) — re-unseal it (§1.B) and re-run the bring-up to recover
> the app. For the demo, `.\demo-up.ps1` wraps the LIVE command below (secret-load + Vault-unseal
> check) and now **verifies every service is running after `up`** — a partial bring-up fails loud
> instead of printing "Stack up". `demo-up.ps1` also adds `--profile mail` (Mailpit at :8025).

```bash
# LIVE (real tokens) — layer the live override + export the two keys first (§1.A→1.C pre-conditions):
export VAULT_TOKEN=<scoped-read-token>          # persistent Vault, RUNBOOK §1.B
export VIGIL_GUIDE_LLM_API_KEY=sk-ant-...        # the Guide's OWN Anthropic key, §1.C
docker compose -f docker-compose.dev.yml -f docker-compose.live.yml \
  --profile app --profile guide --profile frontend up -d --build
```

**How the frontend is wired (important):** it is *only* an HTTP server for the built UI. The
**browser** (running on your host) makes the API and Guide calls, so the `NEXT_PUBLIC_*` base URLs are
**inlined at build time** and point at the **host-published ports** — `NEXT_PUBLIC_API_URL=http://localhost:8000`
and `NEXT_PUBLIC_GUIDE_URL=http://localhost:8080` (the frontend's own code defaults; overridable as
build args / shell env). They are **not** compose service names, and the frontend container needs **no**
app network and **no** `depends_on` — it never reaches app services itself. Full functionality of course
needs the API (:8000) and Guide (:8080) up too (hence the combined profiles above).

> **This is the containerized full-stack, not a replacement for dev-mode.** Local
> `cd frontend && npm run dev` (§1.5) is unchanged and remains the development workflow with HMR; the
> `output: "standalone"` config addition is a **build-only** concern and does not affect `next dev`.
> The image pins **Node 22** (matching CI's `npm ci` against `frontend/package-lock.json`).

### 1.E Live observability — Langfuse tracing + Mailpit email (Gate OBS-MAIL)
Two stubbed-by-default capabilities made REAL, **key-safe and opt-in**. The default stack + CI stay
hermetic (NullTracer + email stub).

**(i) Live Langfuse tracing — keys from the persistent Vault, enabled on the live stack.**
The app reads the Langfuse keys from the **same persistent Vault** that holds the Anthropic key (the
code paths are `secret/vigil/langfuse/public_key` + `secret/vigil/langfuse/secret_key`; the host is
non-secret config, default `https://cloud.langfuse.com`). Seed the two keys ONCE by hand (root token,
never committed):
```bash
ROOT=<persistent-vault-root-token>   # from `operator init`; password manager, never the repo
docker exec vigil-vault-1 sh -c "VAULT_ADDR=http://127.0.0.1:8200 VAULT_TOKEN=$ROOT vault kv put secret/vigil/langfuse/public_key value=pk-lf-..."
docker exec vigil-vault-1 sh -c "VAULT_ADDR=http://127.0.0.1:8200 VAULT_TOKEN=$ROOT vault kv put secret/vigil/langfuse/secret_key value=sk-lf-..."
```
The **live override** (`docker-compose.live.yml`) sets `VIGIL_LANGFUSE_ENABLED=true` (+ overridable
`VIGIL_LANGFUSE_HOST`) on `api`+`worker`, so bringing up the live stack (§1.B) turns the **real
tracer** on; it reads those Vault keys. Tracing is **best-effort**: if the keys are absent or init
fails it falls back to `NullTracer` and the turn (and its mandatory `message_events` row) is never
broken. **Verify:** with the live stack up and keys seeded, drive one assistant turn
(`POST /api/v1/assistant/conversations` → a message → poll the job) → a **trace appears in the
Langfuse project** for the configured host. (For a region host pass `VIGIL_LANGFUSE_HOST=...`.) The
trace carries **redacted** content only (no raw PII), consistent with `message_events`.

**(ii) Real email via Mailpit — self-contained, demo-safe (no external accounts, no spam).**
`docker-compose.dev.yml` has a **`mailpit`** service (profile `mail`): a catch-all SMTP server
(`mailpit:1025` in-network, also published) with a web inbox at **http://localhost:8025**. The
**mail override** (`docker-compose.mail.yml`) flips `api`+`worker` to a **real SMTP send**
(`VIGIL_EMAIL_STUB=false`, `VIGIL_SMTP_HOST=mailpit`, STARTTLS+AUTH **off** → no cert, no credential).
No Vault secret is read on this path. Bring up the **stubbed-LLM** dev app + Mailpit + real SMTP:
```bash
# A FRESH seed already wires a working drift-alert recipient (Gate DRIFT-EMAIL-FIX): the
# mlops_engineer (mlops@vigil.example) is seeded with that address by default, so the drift email
# JUST WORKS with no env var. To route to a REAL demo inbox instead, set VIGIL_DEMO_ML_NOTIFY_EMAIL
# at seed time (it sets both the mlops_engineer's and the platform_admin's notification_email):
#   VIGIL_DEMO_ML_NOTIFY_EMAIL=ml@example.test \
docker compose -f docker-compose.dev.yml --profile tools run --rm seed       # (fresh-volume DBs only)
docker compose -f docker-compose.dev.yml -f docker-compose.mail.yml --profile app --profile mail up -d --build
```
**Trigger a notification (the worker container sends → `mailpit:1025`):**
```bash
# (a) Drift-breach alert (platform): log in as the platform admin, then enqueue a CONSTRUCTED breach:
TOKEN=$(curl -s localhost:8000/api/v1/auth/login -H 'content-type: application/json' \
  -d '{"email":"admin@vigil.example","password":"vigil-dev-password"}' | python -c "import sys,json;print(json.load(sys.stdin)['access_token'])")
curl -s -X POST localhost:8000/api/v1/monitoring/drift/run -H "authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' -d '{"regime":"t2d","demo_shift":0.5}'
# (b) OR a serious-risk crossing via the clinical-ops demo (host run → set SMTP to localhost:1025):
#   VIGIL_EMAIL_STUB=false VIGIL_SMTP_HOST=localhost VIGIL_SMTP_PORT=1025 \
#   VIGIL_SMTP_STARTTLS=false VIGIL_SMTP_AUTH=false VIGIL_NOTIFY_FROM_ADDRESS=vigil-notify@vigil.local \
#   uv run python -m scripts.demo_clinical_ops_loop
```
**Verify:** the PII-free email appears in the Mailpit inbox at **http://localhost:8025**. The default
stack (no `-f docker-compose.mail.yml`) stays `VIGIL_EMAIL_STUB=true` (no send); CI/the spine never
load the override.

> **Production swap (no code change).** Point the SAME SMTP config at a real relay
> (SendGrid/SES/Resend): set `VIGIL_SMTP_HOST`/`VIGIL_SMTP_PORT` to the relay, `VIGIL_SMTP_STARTTLS=true`,
> `VIGIL_SMTP_AUTH=true`, `VIGIL_NOTIFY_FROM_ADDRESS=<verified sender>`, and put the relay credential in
> Vault (`secret/vigil/notifications/email_password`). The `SmtpEmailSender` send path is identical —
> only the target + creds change.

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
| `VIGIL_GUIDE_LLM_STUB` | `false` (config); the compose `guide` service forces **`true`** | `true` → deterministic Guide stub (no network, no key) | `docker-compose.live.yml` sets it **false**, `VIGIL_GUIDE_LLM_PROVIDER=anthropic`, + the Guide's OWN key from `VIGIL_GUIDE_LLM_API_KEY` (§1.C) |
| `VIGIL_GUIDE_LLM_PROVIDER` | `openai_compatible` | which Guide client to build: `openai_compatible` (`/chat/completions`, Bearer) or `anthropic` (native `/v1/messages`, `x-api-key`) | live Guide → **`anthropic`** (its own `sk-ant-...` key) |

Other useful config: `VIGIL_DEMO_MODE` (default `false`; gates `POST /scoring/inject_events`),
`VIGIL_APP_BASE_URL` (default `http://localhost:3000`; the deep-link base in the Phase-9 email),
`VIGIL_LANGFUSE_HOST` (default `https://cloud.langfuse.com`). **SMTP transport (Gate OBS-MAIL):**
`VIGIL_SMTP_STARTTLS` / `VIGIL_SMTP_AUTH` (both default `true` for a real relay; the Mailpit override
sets both `false` for the plaintext/no-auth local catch-all), `VIGIL_SMTP_HOST` / `VIGIL_SMTP_PORT`
(default Gmail `smtp.gmail.com:587`; the override → `mailpit:1025`).

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

### (c) Live Guide turn (isolated service) — Anthropic-native
- **Compose (Gate D3, recommended):** `export VIGIL_GUIDE_LLM_API_KEY=sk-ant-...` then bring up with
  the live override + `--profile guide` (§1.C). The override selects the native Anthropic client
  (`VIGIL_GUIDE_LLM_PROVIDER=anthropic`). The Guide reads the key from its own env (no Vault), stays
  on `guide-net`, and answers `POST :8080/ask` from its approved-doc index only.
- **Host/uv:** set `VIGIL_GUIDE_LLM_API_KEY=sk-ant-...` (or `vault kv put secret/vigil/guide/llm_api_key`)
  + `VIGIL_GUIDE_LLM_PROVIDER=anthropic` + `VIGIL_GUIDE_LLM_MODEL=claude-haiku-4-5`; ensure
  `VIGIL_GUIDE_LLM_STUB` is unset/`false`; (re)build the index `uv run python -m guide.build_index`;
  start `uvicorn guide.app:app --port 8080`. (Leave the provider at its `openai_compatible` default for
  an OpenAI/OpenRouter key instead.)

Then ask an **approved-docs** question against the Guide endpoint.
**Success:** a grounded answer cites approved-doc content; an out-of-scope / low-relevance question
is refused. The Guide reaches ONLY its own approved-doc index — never the app DB, model endpoints, or
app secrets (that is what (d) proves at the network layer).

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
| Containerized frontend (D2) | http://localhost:3000 | `docker compose -f docker-compose.dev.yml --profile frontend up -d --build` (prod build; standalone) |
| **Whole system (infra+app+worker+Guide+UI)** | UI localhost:3000 | `docker compose -f docker-compose.dev.yml --profile app --profile guide --profile frontend up -d --build` (Gate D2, §1.D) |
| Mailpit (real email, demo-safe) | inbox http://localhost:8025 | `docker compose -f docker-compose.dev.yml -f docker-compose.mail.yml --profile app --profile mail up -d --build` (Gate OBS-MAIL, §1.E) |

Make targets: `make db-up`, `make migrate`, `make seed`, `make api`, `make worker`,
`make check-specs`, `make leakage`, `make guide-isolation-proof`.
