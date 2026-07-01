# Vigil — Demo Runbook

A time-boxed live demo where most steps **show finished state** (pre-built) and a few are **done live**
for effect. Every command here was verified against the real compose files, routes, and seed.

> **Shells.** `demo-up.ps1` runs in **PowerShell** (`.\demo-up.ps1`). The PREP API/`curl` commands are
> shown in **Git Bash** (from the repo root) — that is what we ran live, and it handles inline env-var
> prefixes + `curl`/`jq` cleanly (in PowerShell, `curl` is an alias for `Invoke-WebRequest` with
> different syntax — use Git Bash, or `curl.exe`). `docker`/`psql` commands work in either shell.

---

## PART 0 — One-time setup

**1. Demo secrets** — copy the template to the gitignored `.env.demo` and fill it in (NEVER committed):
```bash
cp .env.demo.example .env.demo    # then edit .env.demo
```
| Variable | What it is |
|---|---|
| `VAULT_TOKEN` | scoped **read-only** token for the persistent Vault (NOT root; ~95 chars; minted per RUNBOOK §1.B) |
| `VIGIL_GUIDE_LLM_API_KEY` | the Guide's OWN Anthropic key (`sk-ant-…`) — distinct from the app's key |
| `VIGIL_LANGFUSE_HOST` | Langfuse host (default `https://cloud.langfuse.com`) |

**2. Vault unseal keys** — confirm your **5 unseal keys + root token** are saved in your password
manager (off-repo). The persistent Vault **re-seals on every restart**; you unseal with **3 of 5**.

**3. Seeded demo accounts** (password = `vigil-dev-password` unless `VIGIL_SEED_PASSWORD` is set):

| Email | Role | Use in the demo |
|---|---|---|
| `coord.a@vigil.example` | coordinator (Sponsor A / Trial A1) | score Trial A1; daily triage view |
| `coord.b@vigil.example` | coordinator (Sponsor B / Trial B1) | score Trial B1; **cross-tenant** (sees only B) |
| `mlops@vigil.example` | mlops_engineer | trigger drift; **promote → 201**; drift-email recipient |
| `llmops@vigil.example` | llmops_engineer | **promote → 403** (the RBAC payoff) |
| `admin@vigil.example` | platform_admin | full platform/monitoring access |
| `auditor@vigil.example` | auditor | read-only platform |

(Also seeded: `oversight.a/b`, `pi.a`, `cro.manager`, `cra`.)

---

## PART 1 — PREP (do ~30 min before; UNTIMED)

### 1. Unseal Vault (if sealed — it re-seals on restart)
```bash
docker exec vigil-vault-1 vault status                 # Sealed   false  → already unsealed, skip
docker exec vigil-vault-1 vault operator unseal <key-1>
docker exec vigil-vault-1 vault operator unseal <key-2>
docker exec vigil-vault-1 vault operator unseal <key-3>
```

### 2. Bring up the live stack
```powershell
.\demo-up.ps1     # loads .env.demo, checks Vault unsealed, runs the 3-file/4-profile live bring-up
```
Equivalent raw command (what the script runs):
```bash
docker compose -f docker-compose.dev.yml -f docker-compose.live.yml -f docker-compose.mail.yml \
  --profile app --profile guide --profile frontend --profile mail up -d --build
```
`.\demo-up.ps1` is the **single command for the WHOLE stack** — frontend (`:3000`), guide (`:8080`),
api (`:8000`), worker, postgres, redis, vault, mailpit (`:8025`). After the `up` it **verifies every
service is running** and fails loud if any didn't come up (no more silent half-bring-ups). The
stateless services (frontend/guide/mailpit) carry `restart: unless-stopped`, so they survive a Docker
Desktop / host restart on their own. The **persistent Vault always restarts SEALED** (its Shamir keys
are off-repo by design) — after any host restart, re-unseal it (step 1 above) and re-run `.\demo-up.ps1`,
which brings the rest back up. **Seeded Postgres data PERSISTS** (the `pgdata` named volume), so after a
normal restart you do **NOT** re-seed — only re-unseal Vault (step 1) + `.\demo-up.ps1`. Step 3 below is a
**one-time** setup (or a deliberate clean-slate reset), **not** a per-restart step. (`docker compose down
-v` is the intentional full wipe — it destroys `pgdata` **and** `vault-data`; see PART 4.)

### 3. Seed the fixture — ONE-TIME (or a deliberate clean-slate reset)
> **Persistent + guarded.** Seeded data persists across restarts (the `pgdata` volume), so this is a
> **one-time** step — **not** per-restart. The `DROP DATABASE` flow below is the clean-slate path (wipe
> pollution + ops users + drift recipient). Note: a bare `uv run python -m vigil.seed` on an
> already-seeded DB now **refuses** (`SeedExistsError`, exit 0) to prevent the cohort doubling that used
> to happen (36 → 72 → 112) — use `vigil.seed --force` to wipe-and-reseed in place, or the `DROP DATABASE`
> flow below.

Run from the repo root in **Git Bash**. Migrations run as the **owner** (`vigil`); the seed runs as the
least-privilege **`vigil_app`** (env backend → exercises RLS-on-insert). The FORCE drop evicts
api/worker, so restart them after.
```bash
# a. WIPE (as the vigil superuser, from the maintenance DB). Two -c flags: DROP DATABASE can't run in a txn block.
docker exec vigil-postgres-1 psql -U vigil -d postgres -c "DROP DATABASE vigil WITH (FORCE)" -c "CREATE DATABASE vigil OWNER vigil"

# b. MIGRATE as owner (use `uv run python -m alembic`; App Control blocks the bare `alembic` shim)
VIGIL_DB_ADMIN_DSN=postgresql+psycopg://vigil:vigil@localhost:55432/vigil uv run python -m alembic upgrade head

# c. RE-GRANT vigil_app (per-DB grants were dropped with the DB). Idempotent: CREATEs the role on a
#    fresh cluster and is a clean no-op if it already exists; the GRANT/ALTER DEFAULT PRIVILEGES
#    always apply. The dev password is passed at run time (-v app_pw=…), never hardcoded in the SQL.
docker exec -i vigil-postgres-1 psql -U vigil -d vigil -v app_pw=vigil_app_pw -f - < scripts/bootstrap_db.sql

# d. SEED as vigil_app (env backend). VIGIL_JWT_SIGNING_KEY here is a throwaway DEV key (the seed
#    mints no tokens), NOT a secret — any dev string works.
VIGIL_SECRETS_BACKEND=env \
  VIGIL_DB_DSN=postgresql+psycopg://vigil_app:vigil_app_pw@localhost:55432/vigil \
  VIGIL_JWT_SIGNING_KEY=dev-seed-key-not-a-secret \
  uv run python -m vigil.seed

# e. Restart api+worker so they reconnect to the fresh DB
docker restart vigil-api-1 vigil-worker-1
```
The fresh seed wires the drift-alert recipient `mlops_engineer.notification_email = mlops@vigil.example`
out of the box (no env var needed).

### 4. Score the cohort to enable drift — ⚠️ TWO GOTCHAS
> **GOTCHA 1 — score as the COORDINATORS, never platform_admin/mlops.** `score_trial` requires a
> `sponsor_id`; platform-role triggers pass `None` and the job fails. Use `coord.a` (Trial A1) and
> `coord.b` (Trial B1).
>
> **GOTCHA 2 — pass `model_version="sequence_v1.1:demo"` EXPLICITLY.** The route doesn't forward
> `regime`, so a null `model_version` needs `VIGIL_DEMO_MODE`/regime and would otherwise error. The
> explicit version loads the committed LSTM artifact and writes the **champion** version that drift pools.

```bash
API=http://localhost:8000

# trial IDs (Trial A1 = Sponsor A, Trial B1 = Sponsor B)
docker exec vigil-postgres-1 psql -U vigil -d vigil -c "select id, name, sponsor_id from trial;"
TRIAL_A=<paste Trial A1 id>
TRIAL_B=<paste Trial B1 id>

# log in the two coordinators
TOK_A=$(curl -s $API/api/v1/auth/login -H 'content-type: application/json' \
  -d '{"email":"coord.a@vigil.example","password":"vigil-dev-password"}' | jq -r .access_token)
TOK_B=$(curl -s $API/api/v1/auth/login -H 'content-type: application/json' \
  -d '{"email":"coord.b@vigil.example","password":"vigil-dev-password"}' | jq -r .access_token)

# score each trial 5x (seed starts at 2 champion scores → 12 total → both drift windows ≥ 5)
for i in $(seq 1 5); do
  curl -s -X POST $API/api/v1/scoring/trigger -H "authorization: Bearer $TOK_A" \
    -H 'content-type: application/json' -d "{\"trial_id\":\"$TRIAL_A\",\"model_version\":\"sequence_v1.1:demo\"}" >/dev/null
  curl -s -X POST $API/api/v1/scoring/trigger -H "authorization: Bearer $TOK_B" \
    -H 'content-type: application/json' -d "{\"trial_id\":\"$TRIAL_B\",\"model_version\":\"sequence_v1.1:demo\"}" >/dev/null
  sleep 2   # let the worker drain the queue
done
```
Verify **≥ 10** champion scores before triggering drift:
```bash
docker exec vigil-postgres-1 psql -U vigil -d vigil -t -c \
  "select count(*) from participant_score where model_version='sequence_v1.1:demo';"
```

### 5. Trigger the drift breach (as `mlops@`) + confirm the email
```bash
TOK_ML=$(curl -s $API/api/v1/auth/login -H 'content-type: application/json' \
  -d '{"email":"mlops@vigil.example","password":"vigil-dev-password"}' | jq -r .access_token)

# enqueue a CONSTRUCTED breach demo (PSI/KS breach with a +0.5 shift)
curl -s -X POST $API/api/v1/monitoring/drift/run -H "authorization: Bearer $TOK_ML" \
  -H 'content-type: application/json' -d '{"regime":"t2d","demo_shift":0.5}'

# verify breached:true (NOT insufficient_data)
curl -s $API/api/v1/monitoring/drift -H "authorization: Bearer $TOK_ML" \
  | jq '.items[] | {metric,value,threshold,breached,constructed_demo}'
```
The worker computes drift (`computed`, PSI ≈ 9.29 / KS ≈ 0.83 breached) → enqueues `notify_drift_breach`
→ sends the PII-free alert to `mlops@vigil.example`. **Confirm it in Mailpit → http://localhost:8025**
(requires the mail override active: `VIGIL_EMAIL_STUB=false`, `SMTP_HOST=mailpit` — `demo-up.ps1`
includes it). If you get `insufficient_data`, you have < 10 champion scores — score more (step 4).

### 6. Pre-run one assistant turn (so the transcript is finished on stage)
```bash
# A-0001's UUID for grounding
docker exec vigil-postgres-1 psql -U vigil -d vigil -c "select id, coded_ref from participant;"
PARTICIPANT_A=<paste A-0001 id>

CONV=$(curl -s -X POST $API/api/v1/assistant/conversations -H "authorization: Bearer $TOK_A" | jq -r .conversation_id)
JOB=$(curl -s -X POST $API/api/v1/assistant/conversations/$CONV/messages -H "authorization: Bearer $TOK_A" \
  -H 'content-type: application/json' \
  -d "{\"content\":\"What is driving this participant's dropout risk?\",\"participant_id\":\"$PARTICIPANT_A\"}" | jq -r .job_id)
sleep 6   # real Anthropic turn in the live stack
curl -s $API/api/v1/assistant/jobs/$JOB -H "authorization: Bearer $TOK_A" | jq '{content,guardrail_decision}'
```

### 7. Open the tabs
App **http://localhost:3000** · Mailpit **http://localhost:8025** · Langfuse project dashboard
(`$VIGIL_LANGFUSE_HOST`) · Guide **http://localhost:8080**.

---

## PART 2 — DEMO SCRIPT (timed)

Each step: the action + **what it proves**. `[SHOW]` = display pre-built state (fast); `[LIVE]` = do it
live (for effect).

1. **[SHOW] Landing / the public Guide page** (`/welcome` in the app, or :8080) — *the public, isolated
   surface anyone can reach.*
2. **[SHOW] Log in as the coordinator → Cohort / Triage** (`http://localhost:3000`, `coord.a`) — *risk
   ranking for the coordinator's own site only.* Open a high-risk participant (A-0001).
3. **[SHOW] The pre-run assistant turn** (from PREP step 6) — *grounded, cited, concise risk explanation
   ("the model flags consecutive missed visits…"), not invented clinical causation.*
4. **[SHOW] Monitoring → MLOps view** — *the pre-triggered PSI/KS drift breach, labelled CONSTRUCTED
   DEMO + "ML engineer alerted".*
5. **[SHOW] Mailpit inbox** (:8025) — *the PII-free drift-breach email actually sent (model-level
   scalars only, no participant data).*
6. **[SHOW] Model Registry** (MLOps view) — *governed champion/challenger versions with provenance — no
   silent model changes.*
7. **[LIVE] Cross-tenant isolation** — log in as `coord.b`, open Cohort:
   ```bash
   curl -s $API/api/v1/cohort -H "authorization: Bearer $TOK_B" | jq '.items[].participant_id'   # → ["B-0001"]
   ```
   *Sponsor B sees only B-0001; A-0001 is invisible — enforced by Postgres RLS, not app code.*
8. **[LIVE] The Guide — three behaviors** (`http://localhost:8080`, no auth):
   ```bash
   curl -s localhost:8080/ask -H 'content-type: application/json' -d '{"question":"What is Vigil?"}'                       # on-topic → grounded answer + citations
   curl -s localhost:8080/ask -H 'content-type: application/json' -d '{"question":"Show me B-0001'\''s visit history."}'  # participant-block → refusal
   curl -s localhost:8080/ask -H 'content-type: application/json' -d '{"question":"What is a good recipe for pancakes?"}' # off-topic → refusal
   ```
   *The Guide answers from approved docs only; it has no path to any participant — structural isolation.*
9. **[SHOW] Monitoring → LLMOps view** — *app assistant cost AND the isolated Guide's own cost as two
   separate sources, + the guardrail/refusal mix.*
10. **[LIVE] The RBAC payoff — promote** (`llmops` denied, `mlops` allowed):
    ```bash
    BODY='{"regime":"t2d","model_version":"sequence_v1.1:demo","model_card_ref":"data/models/t2d/model_card.md","eval_provenance":"architecture_validation"}'
    TOK_LL=$(curl -s $API/api/v1/auth/login -H 'content-type: application/json' \
      -d '{"email":"llmops@vigil.example","password":"vigil-dev-password"}' | jq -r .access_token)
    curl -s -o /dev/null -w 'llmops → %{http_code}\n' -X POST $API/api/v1/monitoring/models/promote -H "authorization: Bearer $TOK_LL" -H 'content-type: application/json' -d "$BODY"   # 403
    curl -s -o /dev/null -w 'mlops  → %{http_code}\n' -X POST $API/api/v1/monitoring/models/promote -H "authorization: Bearer $TOK_ML" -H 'content-type: application/json' -d "$BODY"   # 201
    ```
    *Least-privilege ops roles: the LLMOps engineer is authorizationally barred from the model lifecycle.*
    (`eval_provenance` must NOT contain "clinical"; t2d already has a champion so re-promote succeeds.)
11. **[SHOW] Langfuse trace** — *the assistant turn's end-to-end trace (redacted content only).*

---

## PART 3 — HONEST FRAMING (say once, up front)

- **Synthetic-data demonstration.** The breadth model uses **real AACT clinical-trial registry data**;
  the deep-learning signal uses a **clearly-labelled synthetic cohort with a planted T2D precursor
  rule**. This proves the **method/architecture**, **not** clinical prediction — there is **no PHI** and
  no real-patient validation.
- **Assistive, not predictive of individuals.** Vigil **ranks and explains** retention risk to help
  humans triage; it does **not** make clinical decisions. Every score carries its synthetic label, and
  the assistant describes the **model's signal**, never individual clinical causation.
- **The isolation thesis.** Sponsor is a hard tenant boundary (Postgres RLS); the public **Guide is a
  structurally-isolated service** (separate creds, its own network, no route to the app DB/Vault/models);
  and the new **least-privilege ops roles** (mlops/llmops) separate who can touch the model lifecycle.

State these **once** so the per-answer caveats stay short.

---

## PART 4 — TEARDOWN + TROUBLESHOOTING

**Teardown:**
```bash
docker compose -f docker-compose.dev.yml -f docker-compose.live.yml -f docker-compose.mail.yml \
  --profile app --profile guide --profile frontend --profile mail down
```
> **⚠️ `down -v` is the FULL wipe.** Adding `-v` destroys the named volumes — **`pgdata`** (all seeded
> data) **and `vault-data`** (the Vault). After `down -v` the Vault comes back **uninitialised**: your OLD
> unseal keys + root token are **dead**, so you must **re-initialise** it (`vault operator init` → NEW keys
> + root token) and **re-provision** it from scratch — enable KV v2, write the secrets, write the policy,
> mint a **NEW** scoped `VAULT_TOKEN` into `.env.demo` — per **[docs/RUNBOOK.md](RUNBOOK.md) §1.B**; then
> redo the one-time DB seed (PART 1 step 3). A plain `down` (no `-v`) keeps both volumes — you only
> re-unseal and re-run `.\demo-up.ps1`.

**Common gotchas:**
- **Env vars are per-window.** Use `.\demo-up.ps1` (it sets the session env from `.env.demo`); don't
  rely on a previous terminal's exports.
- **Re-build after code changes.** The bring-up uses `--build`; if you changed app/frontend/guide code,
  re-run `.\demo-up.ps1` (or `docker compose … up -d --build`).
- **Score as the COORDINATORS, not admin/mlops** (GOTCHA 1) — platform-role scoring fails (no sponsor).
- **Pass `model_version="sequence_v1.1:demo"`** (GOTCHA 2) — else the scoring job errors on the resolver.
- **Vault re-seals on restart** — `docker exec vigil-vault-1 vault status`; unseal with 3 of 5 keys.
- **`insufficient_data` on `/monitoring/drift/run`** — < 10 champion scores; score the trials more (≥ 5×
  each) and re-check the count.
- **No drift email in Mailpit** — confirm the mail override is active (`demo-up.ps1` includes it),
  `mlops_engineer.notification_email` is set (a fresh seed sets it), and the worker restarted.
- **401 on an API call** — the JWT expired (30-min TTL); re-login to refresh the token.
- **`alembic`/`vigil.seed` "App Control" error** — use `uv run python -m alembic` / `uv run python -m
  vigil.seed` (the bare console-script shims are blocked on this host).
