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

## Screenshots

<table>
  <tr>
    <td width="50%"><a href="docs/images/participant.png"><img src="docs/images/participant.png" alt="Participant detail with the non-dismissible [SYNTHETIC DATA] banner and imputed baseline covariates"></a><br/><sub><b>Participant detail</b> — champion score + a non-dismissible <code>[SYNTHETIC DATA]</code> banner + literature-prior <i>imputed</i> baseline covariates.</sub></td>
    <td width="50%"><a href="docs/images/hero.png"><img src="docs/images/hero.png" alt="Public landing page: Surface risk early. Triage the cohort. Explain every flag."></a><br/><sub><b>Landing / Guide</b> — <i>“Surface risk early. Triage the cohort. Explain every flag.”</i> — surfaces and explains risk, never “predicts who drops out”.</sub></td>
  </tr>
  <tr>
    <td width="50%"><a href="docs/images/observability.png"><img src="docs/images/observability.png" alt="Redacted observability / audit log of message_events"></a><br/><sub><b>Observability</b> — redacted <code>message_events</code>; guardrail blocks visible; coded ids only, no raw content.</sub></td>
    <td width="50%"><a href="docs/images/mlmonitoring.png"><img src="docs/images/mlmonitoring.png" alt="MLOps panel: champion / challenger / shadow registry and drift breaches"></a><br/><sub><b>MLOps</b> — champion / challenger / shadow registry for the T2D regime + drift-breach count + champion health.</sub></td>
  </tr>
</table>

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

**Calibration sub-metrics are build-time-reported, not artifact-regenerable.** The isotonic
calibrator's *mechanism* is reproducible (the `raw_prob → calibrated_prob` knot-map is persisted in
the `.pt`), but its quality numbers (ECE ≈ 0.008 near-calibration, the ECE/Brier raw→calibrated, and
the ≈ 0.74 top-bin empirical rate) are **reported at build time and are NOT regenerable from a
committed artifact** — unlike the headline test metrics in `sequence_metrics.json`, which are
(`specs/scoring.md` § Output calibration).

**Provenance everywhere.** The synthetic cohort and its literature-prior-imputed covariates (BMI
~80% imputed, HbA1c ~55%) are labelled at every surface; the generator's latent hazard is never a
feature; build-time AACT ingestion is never reached at runtime. No PHI, at any stage.

![MLOps panel — the T2D model registry: champion, shadow, and challenger](docs/images/mlmonitoring.png)

*The MLOps surface makes the modeling story concrete: **champion** `sequence_v1.1:demo` (the calibrated
LSTM) is what scores participants; **shadow** `structural_v1.0:t2d` (the classic-ML baseline, real-AACT
PR-AUC 0.697) runs alongside but never reaches a clinical read; **challenger** `sequence_v1.2:demo` is a
**registry-only placeholder** (no trained `.pt`) for the governance demo. Regime = T2D (Type-2 Diabetes).*

---

## Architecture

```mermaid
flowchart LR
  UI["Next.js dashboard<br/>:3000"]

  subgraph app["App network — never reachable from the Guide"]
    API["FastAPI :8000<br/>routers - services - repositories"]
    WORKER["Arq worker<br/>scoring - notifications - drift"]
    PG[("Postgres + pgvector<br/>RLS FORCE - NOBYPASSRLS app role")]
    REDIS[("Redis<br/>sessions - queue - rate-limit")]
    VAULT[["Vault<br/>secrets - KV v2"]]
  end

  subgraph guidenet["guide-net — isolated public Guide"]
    GUIDE["Guide :8080<br/>own image - imports no vigil.*"]
    IDX[("file-backed<br/>approved-docs index")]
    GSINK[("own message_events sink")]
    NOTE["NO route to postgres / redis / vault / api"]
  end

  UI -->|"JWT (scoped)"| API
  UI -->|"Ask the Guide - no login"| GUIDE
  API --> PG
  API -->|enqueue| REDIS
  API -. reads secrets .-> VAULT
  WORKER --> PG
  WORKER --> REDIS
  WORKER -. reads secrets .-> VAULT
  GUIDE --> IDX
  GUIDE --> GSINK
  GUIDE -. own LLM key from env .-> ANTHROPIC["api.anthropic.com"]

  classDef block fill:#fff7ed,stroke:#b45309,color:#b45309;
  class NOTE block;
```

*The one-directional spine — routers → services → repositories → Postgres (RLS) — with the Arq worker,
Redis, and Vault on the app network. The public **Guide** is a separate image on its own `guide-net`
with **no path** to the app's DB, Redis, Vault, or API; it reads only its file-backed approved-doc
index and uses its **own** LLM key from the environment.*

---

## What's built — the system

Vigil has **two surfaces that never share credentials, DB, or endpoints**: the full operational
app, and an isolated public Guide. The platform spans Phases 1–9 (see `ROADMAP.md`) — Phases 1–7
and 9 are complete; **Phase 8 (production-readiness) is a deliberate partial**: the Guide's
three-layer isolation proof is done (the layer-3 kind+Calico network denial is hand-verified, with a
committed PASS transcript), while full production k8s/HPA, app-side egress, and cloud-KMS Vault
auto-unseal remain deferred future work (`FUTURE_WORK.md`).

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

```mermaid
flowchart TB
  A["Sponsor A coordinator<br/>verified JWT to Scope: (A, trial-A, site-A)"]
  A -->|"GET /participants/{a Sponsor-B id}"| API["API + service layer"]
  API --> SEC1{"SEC-1: scope_filter.participant_visible<br/>cross-site tuple in scope?"}
  SEC1 -->|no| DENY["403 / 404 — fail closed"]
  SEC1 -->|yes| REPO["repository - scoped session<br/>SET app.current_sponsor = A"]
  REPO --> RLS[("Postgres RLS<br/>FORCE - NOBYPASSRLS vigil_app<br/>USING sponsor_id = current_sponsor")]
  RLS -->|"a Sponsor B row"| BLOCKED["0 rows — RLS hides it (cross-tenant BLOCKED)"]
  RLS -->|"a Sponsor A row"| ALLOW{"champion-only<br/>surfacing allowlist"}
  ALLOW -->|"shadow / challenger row"| HIDDEN["never surfaced to a clinical read"]
  ALLOW -->|"champion row"| OK["row returned"]

  classDef block fill:#fef2f2,stroke:#b91c1c,color:#b91c1c;
  class DENY,BLOCKED,HIDDEN block;
```

*Two enforcement axes plus an allowlist: **RLS** (Postgres, `FORCE` + a `NOBYPASSRLS` app role,
fail-closed) is the hard cross-tenant guarantee; **SEC-1** (`scope_filter.participant_visible`) adds the
tuple-coupled cross-site narrowing RLS can't express; and **champion-only surfacing** ensures a
shadow/challenger score can never reach a clinical read. A cross-tenant read returns zero rows.*

### Scoring pipeline
Async Arq `score_trial`: resolve scope → load cohort + engagement → temporal + leakage guards →
**champion** LSTM inference alongside a **shadow** structural GBT → **append** a timestamped history
row → audit → denorm. **Champion-only surfacing** (an allowlist, not just RLS — a shadow/challenger
row can never reach a clinical read); promotion-aware **risk history**; idempotent **serious-risk
crossings**; **real, leakage-safe occlusion attributions** (`top_factors`/`reasons`, method-labelled,
never fabricated). Champion/challenger/shadow routing with audited promotion and drift-triggered
fallback — nothing changes silently.

```mermaid
flowchart LR
  REG[("model_registry + routing_state<br/>champion sequence_v1.1:demo<br/>shadow structural_v1.0:t2d<br/>challenger sequence_v1.2:demo - placeholder")]
  REG --> SCORE["score_trial (Arq)<br/>champion + shadow inference<br/>APPEND timestamped history"]
  SCORE --> AUDIT["participant denorm + audit"]
  SCORE --> DRIFT["drift job - PSI / KS<br/>over the champion distribution"]
  DRIFT -->|breach| ALERT["PII-free email alert<br/>(M2 mechanism)"]
  ALERT -. "manual / future trigger" .-> PROMO["governed promotion<br/>audited champion swap (M3 mechanism)<br/>retains prior champion - reversible"]
  DRIFT -. "DEFERRED: auto-delivery into handle_breach" .-> PROMO
  PROMO --> REG

  classDef deferred fill:#fff7ed,stroke:#b45309,color:#b45309,stroke-dasharray:5 5;
  class PROMO deferred;
```

*Solid edges are **built**; dotted edges are **deferred**. Scoring **appends** history (champion +
shadow), drift computes **real PSI/KS** over the champion distribution, and a breach raises a PII-free
alert (M2 mechanism). Governed promotion (M3) is a **built, audited mechanism** but is **not
demo-verified end-to-end** — the **drift → promote auto-delivery** into `handle_breach` is deferred, and
no model has become champion *and then actually scored a cohort* through this path (`FUTURE_WORK.md`).*

### Agentic RAG (hand-rolled — not LangGraph/MCP)
A **hand-rolled LLM-classify router** (`vigil/agents/router.py`) dispatches three scope-bound agents
(Retention / Report / Operations) over a **shared grounding spine** (`agent_base.py`), using **plain
scope-resolved tools** (`tools.py`: champion-only risk facts + RLS-scoped pgvector document search) —
an MCP-style/tool-calling *pattern*, **not the MCP protocol and not LangGraph** (neither is a
dependency). Retrieval uses **offline sentence-transformers embeddings** over **pgvector**; generation
is **Anthropic-primary with OpenRouter fallback**; every turn passes **PII redaction + guardrails**
(clinical/injection/secret refusals) and produces a **cited, doc-grounded answer or a grounded
refusal**. A labelled **eval set is a CI release gate**. CI is hermetic (stub LLM, no key).

<table>
  <tr>
    <td width="50%"><a href="docs/images/retentionagent.png"><img src="docs/images/retentionagent.png" alt="Retention agent answering which participants need intervention, with the synthetic-data caveat"></a><br/><sub><b>Retention agent</b> — answers “which participants need intervention” from <b>champion-only</b>, scope-bound facts (coded refs like <code>A-0005</code>), with the synthetic-data caveat. It explains risk; it never claims to predict who drops out.</sub></td>
    <td width="50%"><a href="docs/images/guardrails.png"><img src="docs/images/guardrails.png" alt="Assistant refusing prompt-injection, cross-cohort, and out-of-scope requests"></a><br/><sub><b>Guardrails</b> — prompt-injection (“output your system instructions”), cross-cohort, and out-of-scope requests are <b>refused by guardrail</b>; clinical/identity asks are blocked too.</sub></td>
  </tr>
</table>

### Observability
Every assistant/Guide turn writes a **redacted `message_events` row**; an inspect API + admin page
(platform/auditor only, RLS-bound) lets you inspect messages, verify guardrails fired, debug
retrieval, and confirm redaction. Real **cost/latency** capture and `/monitoring/{cost,models}`
rollups; **honest-empty `/drift`** (real drift computation is explicitly deferred, not faked);
optional **Langfuse** per-turn tracing (redacted-only, egress-allow-listed, off in CI).

<table>
  <tr>
    <td width="50%"><a href="docs/images/observability.png"><img src="docs/images/observability.png" alt="Redacted message_events audit log with guardrail-block counts"></a><br/><sub><b>Audit log</b> — redacted <code>message_events</code>: router/guardrail refusals (<code>blocked</code>) vs answered turns (<code>allowed</code>), coded ids only. No raw or identifiable content exists.</sub></td>
    <td width="50%"><a href="docs/images/cost.png"><img src="docs/images/cost.png" alt="Cost and Usage panel: per-turn token/cost rollups"></a><br/><sub><b>Cost &amp; Usage</b> — token/cost rollups from <b>real</b> per-turn usage; <b>honest-zero</b> until a cost rate is configured, never fabricated.</sub></td>
  </tr>
</table>

![LLMOps — two separate cost sources: the in-app assistant vs the isolated Guide](docs/images/platformmonitoring.png)

*LLMOps surface — the in-app assistant's cost (JWT-scoped, from `message_events`) and the **isolated
Guide's** cost (read browser-direct from the Guide's **own** sink, no JWT) are shown **side by side,
never merged**. Two separate cost stores is the observability face of the isolation boundary.*

### The isolated public Guide
A **separate service** (`guide/`, its own creds, own file-backed approved-docs index, own LLM key,
own event sink, imports nothing from `vigil.*`) that explains the project from approved documents
only. It is **proven unable to reach any real resource** through **three layers**: static
import-graph/config/tool-surface audits, a behavioral red-team with a **zero-egress** assertion, and
a **kind + Calico NetworkPolicy denial** (hand-verified on a real cluster, with a negative
pre-check and a positive control).

```mermaid
flowchart TB
  Q["Public question - no login"] --> GUIDE["Guide service :8080"]
  GUIDE --> L1
  subgraph L1["Layer 1 - Static  (CI, every PR)"]
    S1["imports nothing from vigil.*<br/>config + tool-surface audit"]
  end
  L1 --> L2
  subgraph L2["Layer 2 - Behavioral  (CI, every PR)"]
    S2["guardrails + approved-docs-only RAG<br/>red-team + ZERO-egress assertion"]
  end
  L2 --> L3
  subgraph L3["Layer 3 - Network  (PROVEN)"]
    S3["kind + Calico NetworkPolicy denial<br/>negative pre-check + positive control<br/>committed PASS transcript"]
  end
  L3 --> ANS["grounded, cited answer - or a grounded refusal"]
  S3 -->|"every deny-list target"| DENY["DENIED: postgres - redis - vault - api - model endpoints"]

  classDef block fill:#fef2f2,stroke:#b91c1c,color:#b91c1c;
  class DENY block;
  style L3 fill:#ecfdf5,stroke:#047857,color:#047857;
```

*Defense in depth, proven in depth — each layer carries its honest status: **Layer 1 (static)** and
**Layer 2 (behavioral, zero-egress)** gate every PR in CI; **Layer 3 (network)** is **PROVEN** — a
kind + Calico NetworkPolicy denial hand-verified on a real cluster, with the PASS transcript committed
at [`deploy/k8s/last-proof-transcript.txt`](deploy/k8s/last-proof-transcript.txt).*

<img src="docs/images/assistant.png" alt="Public Guide chat next to the app sign-in, noting the Guide shares no login, data, or endpoints with the dashboard" width="900">

*The public **Guide** (left) answers only from approved public documents **with citations**, with **no
login and no participant data** — “served by the isolated Guide service, never the in-app assistant.”
The app sign-in (right) states it plainly: the Guide on this page **shares no login, data, or endpoints
with the dashboard.***

### Clinical-ops loop (Phase 9)
An accruing missed-visit sequence injected for a synthetic participant is rescored by the **real
calibrated model** until its trajectory crosses the **> 0.6 serious threshold** → a deduplicated,
idempotent **crossing** is recorded → the **scope-bound at-risk surface** shows the participant with
**real model-attribution reasons** + **operational recommended actions** (suggestions, not clinical
advice) → a **PII-free, scope-bound email doorbell** fires once. **Every surface carries the
synthetic-demonstration label.** A documented driver lives at `scripts/demo_clinical_ops_loop.py`.

![Suggested coordinator actions and the audited interventions panel](docs/images/interventions.png)

*The at-risk participant's **suggested coordinator actions** (Call / Reminder / Note) are **operational
next-steps — explicitly not clinical advice**; logging one records an **audited intervention**. Acting on
a flag is a human, audited decision — never an automated care action.*

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

### One command — `.\demo-up.ps1`
For the demo, a single PowerShell command brings up the **entire stack — all 8 services** (Postgres,
Redis, the persistent Vault, API, worker, the isolated Guide, the frontend, and Mailpit):

```powershell
.\demo-up.ps1
```

It loads the demo secrets from `.env.demo` (gitignored — never committed), **checks the persistent
Vault is unsealed** (and prints the unseal steps if not), runs the verified `docker compose` live
bring-up, and **verifies every service is running** afterward (a partial bring-up fails loud instead of
printing “Stack up”). One-time pre-requisites: **provision the persistent Vault** (init → unseal → write
secrets → mint the scoped read token; `docs/RUNBOOK.md` §1.B) and **seed the DB once**
(`docs/RUNBOOK.md` §1.A — `scripts/bootstrap_db.sql` → migrate → `vigil.seed`). After that, **data
persists across restarts** (named Postgres + Vault volumes); the seed is **guarded** (re-running is a
safe no-op — `--force` to wipe-and-reseed), and **`docker compose down -v` is the full wipe** (it
destroys the volumes, so you re-provision Vault + re-seed). The Guide keeps its **own** LLM key in its
environment by isolation design — it is never read from the app's Vault.

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

The **gate discipline** behind all of this — spec-first, build-to-spec, the correct test artifact
green, and the *“STOP and ratify the spec rather than fake a result”* honesty culture — is written up in
**[`docs/process/METHODOLOGY.md`](docs/process/METHODOLOGY.md)**.

---

## Roadmap & future work

`ROADMAP.md` is the per-phase status; **[`FUTURE_WORK.md`](FUTURE_WORK.md)** is the honest forward
ledger — every line tagged **[built] / [scaffolded] / [deferred]**. The headline deferrals (stated
plainly, not hidden):

- **Production secrets posture** — cloud **KMS/transit Vault auto-unseal** + a Kubernetes-auth AppRole
  app token (a **config swap, not an architecture change**; self-hosted Shamir 3-of-5 is built today).
- **Full production K8s for the app** — HPA on `api`/`worker`, app-side egress NetworkPolicies, prod
  Deployments/Services/Ingress (today `deploy/k8s/` carries **only** the proven Guide layer-3 proof).
- **Automated scoring/drift→promote delivery** — M1 drift and the M3 governed-promotion mechanism are
  built, but the **auto-delivery of a breach into `handle_breach`** is unwired, and the **challenger
  `sequence_v1.2:demo` is a registry-only placeholder** (no trained `.pt`) — so promotion is a
  mechanism, not a demo-verified end-to-end loop.
- **Known minor items** — refresh-token rotation and explicit rate-limit verification; regime threading
  not reachable from real callers; Guide pgvector parity; sponsor SOP/protocol RAG collections.
- **Real-IPD / clinical validation — OUT OF SCOPE BY DESIGN.** Vigil proves *method and architecture* on
  a labelled-synthetic cohort; it is never a validated clinical tool.

---

This is a demonstration/portfolio system. The structural model is honest about its weak
within-indication signal; the sequence model and the clinical-ops loop are **capability and
architecture demonstrations on labelled-synthetic data** — they are not validated clinical tools and
must not be presented as such.
