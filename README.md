# Vigil — clinical-trial retention intelligence

Vigil surfaces and **explains** participant dropout risk in clinical trials, ranks a cohort for
triage, and routes serious-risk crossings to the right people — on a production-shaped,
multi-tenant platform. It is built with an explicit **honesty discipline**: every number is
reported with its uncertainty and provenance, and the parts that run on synthetic data are
labelled as **capability/architecture demonstrations, not validated clinical tools**.

> **Honest framing (read first).** This is a portfolio/demonstration system built on **real public
> ClinicalTrials.gov/AACT registry data** plus a **clearly-labelled synthetic cohort**. The
> structural between-trial signal is real but weak within indication; the per-visit sequence model
> and the clinical-ops loop run on synthetic, planted-signal data and demonstrate the *method and
> architecture* — they are **NOT clinical predictions**. No PHI is used anywhere.

---

## The honest modeling story

All numbers below are reproduced from the committed artifacts/cards
(`data/models/PHASE3_CARD.md`, `data/models/baselines/metrics.json`,
`data/models/t2d/sequence_metrics.json`) and were re-verified for this README.

**Structural, between-trial signal (real data).** A pan-indication gradient-boosted model on the
real registry reaches a **test PR-AUC of 0.697** — but we report it with its **base-rate-adjusted
skill ≈ 0.42** (test positive prevalence 0.474; skill = (0.697 − 0.474)/(1 − 0.474)) and a
**per-indication bootstrap-CI decomposition** (2,000 resamples, fixed seed). That decomposition
shows the pooled 0.697 is **substantially inflated by between-indication base-rate variation**:
within an indication the structural signal is weak, and **T2D (0.38, CI [0.32, 0.45]) and MDD
(0.32, CI [0.23, 0.44]) sit entirely below their own within-indication chance line**. Structural
covariates predict *which indication* drops out more than *which participant within a trial* will.
(Computed on the modelling cohort — ~37k trials / ~97k arms — drawn from the full cleaned 73,073-trial
/ 182,240-arm `ref_*` tables.)

**Per-visit sequence model (synthetic cohort — a method demonstration).** A causal LSTM dropout
classifier over each participant's visit trajectory, trained on a **clearly-labelled synthetic
cohort** calibrated to real T2D aggregate statistics with a **planted** disengagement→dropout rule.
It reaches **test PR-AUC 0.339**, **+0.088 over the structural-only bar (0.251)** and past its
pre-registered bar (0.3006) — the one real discriminative lift in the modelling work. The champion
(`sequence_v1.1:demo`) is **isotonically calibrated** on a held-out `val` fold (disjoint from train
and test) so its probabilities span a usable range; the calibration is monotonic, so **discrimination
is provably unchanged** (test ROC-AUC invariant, Δ ≈ 5e-5). This is an **architecture/method-validity
demonstration on synthetic, planted-signal data — explicitly NOT a clinical prediction**, and it
does not validate the dropout-precursor hypothesis on real participants.

**The survival model is an honest negative result.** A discrete-time hazard model gives a
**C-index of 0.40** (≈ chance, slightly anti-concordant) — adding time-to-event *ranking* over
event *detection* produced no discrimination gain on this synthetic cohort. Reported plainly, not
hidden.

**Provenance everywhere.** The synthetic cohort and its literature-prior-imputed covariates (BMI
~80% imputed, HbA1c ~55%) are labelled at every surface; the generator's latent hazard is never a
feature; build-time AACT ingestion is never reached at runtime. No PHI, at any stage.

---

## What's built — the system

Vigil has **two surfaces that never share credentials, DB, or endpoints**: the full operational
app, and an isolated public Guide. The platform spans Phases 1–9 (see `ROADMAP.md`).

### Multi-tenant platform & isolation
- **Sponsor is the hard tenant boundary**, enforced by **Postgres row-level security** (FORCE RLS,
  fail-closed; the app connects as a non-superuser `NOBYPASSRLS` role so RLS genuinely binds).
- A **second cross-site scope axis** (SEC-1, `scope_filter.participant_visible`) narrows site-scoped
  roles to their own site/trial — RLS gives the cross-tenant guarantee, the service layer adds the
  tuple-coupled cross-site one.
- **JWT auth** (scope derived from the verified token, never asserted by the client), Redis-revocable
  sessions, **7 roles**, secrets from **Vault**.
- The **sacred cross-tenant + cross-site leakage tests gate every release** (create as sponsor A,
  authenticate as sponsor B / another site, assert invisible).

### Scoring pipeline
Async Arq `score_trial`: resolve scope → load cohort + engagement → temporal + leakage guards →
**champion** LSTM inference alongside a **shadow** structural GBT → **append** a timestamped history
row → audit → denorm. **Champion-only surfacing** (an allowlist, not just RLS — a shadow/challenger
row can never reach a clinical read); promotion-aware **risk history**; idempotent **serious-risk
crossings**; **real, leakage-safe occlusion attributions** (`top_factors`/`reasons`, method-labelled,
never fabricated). Champion/challenger/shadow routing with audited promotion and drift-triggered
fallback — nothing changes silently.

### Agentic RAG (hand-rolled — not LangGraph/MCP)
A **hand-rolled LLM-classify router** (`vigil/agents/router.py`) dispatches three scope-bound agents
(Retention / Report / Operations) over a **shared grounding spine** (`agent_base.py`), using **plain
scope-resolved tools** (`tools.py`: champion-only risk facts + RLS-scoped pgvector document search) —
an MCP-style/tool-calling *pattern*, **not the MCP protocol and not LangGraph** (neither is a
dependency). Retrieval uses **offline sentence-transformers embeddings** over **pgvector**; generation
is **Anthropic-primary with OpenRouter fallback**; every turn passes **PII redaction + guardrails**
(clinical/injection/secret refusals) and produces a **cited, doc-grounded answer or a grounded
refusal**. A labelled **eval set is a CI release gate**. CI is hermetic (stub LLM, no key).

### Observability
Every assistant/Guide turn writes a **redacted `message_events` row**; an inspect API + admin page
(platform/auditor only, RLS-bound) lets you inspect messages, verify guardrails fired, debug
retrieval, and confirm redaction. Real **cost/latency** capture and `/monitoring/{cost,models}`
rollups; **honest-empty `/drift`** (real drift computation is explicitly deferred, not faked);
optional **Langfuse** per-turn tracing (redacted-only, egress-allow-listed, off in CI).

### The isolated public Guide
A **separate service** (`guide/`, its own creds, own file-backed approved-docs index, own LLM key,
own event sink, imports nothing from `vigil.*`) that explains the project from approved documents
only. It is **proven unable to reach any real resource** through **three layers**: static
import-graph/config/tool-surface audits, a behavioral red-team with a **zero-egress** assertion, and
a **kind + Calico NetworkPolicy denial** (hand-verified on a real cluster, with a negative
pre-check and a positive control).

### Clinical-ops loop (Phase 9)
An accruing missed-visit sequence injected for a synthetic participant is rescored by the **real
calibrated model** until its trajectory crosses the **> 0.6 serious threshold** → a deduplicated,
idempotent **crossing** is recorded → the **scope-bound at-risk surface** shows the participant with
**real model-attribution reasons** + **operational recommended actions** (suggestions, not clinical
advice) → a **PII-free, scope-bound email doorbell** fires once. **Every surface carries the
synthetic-demonstration label.** A documented driver lives at `scripts/demo_clinical_ops_loop.py`.

### Frontend
A **Next.js** dashboard wired to the real scoped API and role-gated: login, cohort dashboard,
triage, participant detail (champion score + cross-version risk-trajectory sparkline + reasons +
recommended actions + a non-dismissible synthetic banner), the at-risk list, monitoring, costs, an
observability/admin page, and a docked assistant panel. Honest loading/empty/refusal/error states —
never fabricated rows or answers.

---

## Tech stack

- **Backend / web:** Python 3.12, FastAPI, Uvicorn, Pydantic v2 + pydantic-settings, SQLAlchemy 2,
  Alembic, PyJWT (crypto), argon2-cffi.
- **Data layer:** PostgreSQL (pgvector/pg16) via psycopg 3, SQLAlchemy + Alembic (row-level
  security from the first migration), Redis + **Arq** worker queue, **HashiCorp Vault** (hvac).
- **Data / ML:** NumPy, pandas, PyArrow, SciPy, scikit-learn, SHAP, **PyTorch** (sequence LSTM),
  lifelines (survival).
- **AI / RAG:** **Anthropic Claude** (primary) + **OpenRouter** (fallback), **sentence-transformers**
  (`all-MiniLM-L6-v2`, offline) over **pgvector**, **Langfuse** tracing; stdlib `smtplib` for the
  PII-free notifier.
- **Frontend:** TypeScript, **Next.js**, **React**, **Tailwind CSS**, shadcn/ui + Base UI.
- **Infra / tooling:** Docker + Docker Compose, **Kubernetes** (kind + Calico + kustomize, for the
  Guide isolation proof), **GitHub Actions** CI, **uv**, **ruff**, **pytest**, marimo (EDA).

The agent orchestration is intentionally hand-rolled (no LangGraph/MCP dependency); pgvector is used
rather than a hosted vector DB.

---

## Running it

The full, ordered bring-up (Docker stack → Vault unseal → migrate → seed → API/worker/frontend/Guide),
the exact Vault secret paths, the stub flags, and the run-once **live-keys verification checklist**
(live Anthropic turn, Langfuse trace, Guide turn, the kind+Calico isolation proof, the Phase-9 email)
live in **[`docs/RUNBOOK.md`](docs/RUNBOOK.md)**. A quick orientation:

```
make db-up            # Postgres + Redis (docker-compose.dev.yml)
uv run alembic upgrade head            # apply the RLS schema
uv run python -m vigil.seed            # two-sponsor isolation fixture
uv run uvicorn vigil.api.app:app --reload --port 8000   # API
uv run arq vigil.workers.settings.WorkerSettings        # worker (2nd terminal)
cd frontend && npm install && npm run dev               # frontend on :3000
uv run uvicorn guide.app:app --port 8080                # isolated public Guide
```

Tooling is **Python 3.12 managed with [uv](https://docs.astral.sh/uv/)** (`uv sync` → `.venv/`;
run everything via `uv run …`). Secrets come from **Vault** (`VIGIL_SECRETS_BACKEND=vault`) or, for
a quick hermetic run, the env shim (`VIGIL_SECRETS_BACKEND=env`, as CI uses).

### Ingestion golden set (real, committed)
The clean → synthetic → features pipeline runs offline against the **golden set**: a frozen slice of
**REAL PUBLIC ClinicalTrials.gov/AACT** trial-level data (snapshot `2026-06-05`) committed with its
expected cleaned `ref_*` output. It is the ingestion clean-transform oracle
(`assert_frame_equal(clean_snapshot(raw), expected/)`) and the non-live pipeline substrate. **NO
PHI, NO synthetic rows.** It lives at `tests/golden/` and rebuilds via `make golden`. Per
`specs/data.md` "Evaluation contract" the golden set is solely the transform oracle (golden =
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
`vigil-dev-root` — override it with `<ROOT_TOKEN>` now that dev mode is gone.) The full secret set
(Anthropic, Guide LLM, Langfuse, the Phase-9 SMTP App Password) is enumerated in `docs/RUNBOOK.md`.

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

---

## Repo layout
- `vigil/` — the operational app: `api/` (routers) → `services/` → `repositories/` → `db/`, plus
  `core/` (config, security, scope, logging), `workers/` (Arq), `agents/` (the hand-rolled RAG layer).
- `guide/` — the isolated public Guide (separate service; imports nothing from `vigil.*`).
- `ingestion/` — AACT fetch/clean + synthetic-cohort generation + calibration + EDA.
- `models/` — the Phase-3 modelling (baselines, T2D sequence LSTM, survival, calibration, cards).
- `specs/` — the contracts (source of truth); `scripts/check_specs.py` verifies conformance.
- `frontend/` — the Next.js dashboard.
- `deploy/k8s/` — the Guide layer-3 NetworkPolicy isolation proof (kind + Calico).
- `tests/` — `golden/` (transform oracle), `spine/` (Postgres-backed RLS/scoring/agent/isolation),
  `guide/` (isolation suite), `eval/` (RAG eval sets), plus unit suites.
- `data/models/PHASE3_CARD.md` — the consolidated, honest modelling scorecard.
- `docs/RUNBOOK.md` — full bring-up + live-keys checklist + demo/defense flow.
- `CLAUDE.md` — project memory/contract; `ROADMAP.md` — phase-by-phase status.

## Tests & gates
- `make check-specs` — spec conformance (10 specs, required sections present).
- `uv run ruff check .` / `uv run ruff format --check .` — lint + format.
- `uv run python -m pytest -m "not slow"` — fast unit + ingestion suite.
- `uv run python -m pytest tests/spine -m "slow or not slow"` — the full Postgres-backed spine
  (RLS, scoring, agents, isolation, the sacred leakage tests) including the torch/LSTM invariants.
- `make golden` — rebuild the ingestion transform oracle; `make guide-isolation-proof` — the
  kind+Calico Guide isolation proof. CI (GitHub Actions) runs ruff, check_specs, the fast suite,
  the slow-inclusive spine against a Postgres service, and the frontend typecheck.

---

This is a demonstration/portfolio system. The structural model is honest about its weak
within-indication signal; the sequence model and the clinical-ops loop are **capability and
architecture demonstrations on labelled-synthetic data** — they are not validated clinical tools and
must not be presented as such.
