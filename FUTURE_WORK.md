# Vigil — Future Work & Build-State Ledger

The "pick it back up cleanly later" doc. `ROADMAP.md` is the source of truth for *what shipped*;
this file is the forward-looking complement that states **honestly** what is solid, what is only
partially built, and what was deliberately deferred.

Every line is tagged:
- **[built]** — working and covered by a green test artifact.
- **[scaffolded]** — a real foundation exists but it is **NOT complete**; do not mistake it for done.
- **[deferred]** — a genuine future phase, intentionally not built (some out of scope by design).

Honesty discipline (per `CLAUDE.md`): synthetic stays labelled synthetic; nothing here claims a
clinical capability the system does not have.

---

## 1. BUILT & solid

- **[built]** Specs as the contract + per-phase ritual + conformance gate — 10 specs in
  [specs/](specs/), verified by [scripts/check_specs.py](scripts/check_specs.py) (`make check-specs`).
- **[built]** Real-AACT **build-time** ingestion + a clearly-labelled **synthetic cohort** —
  [ingestion/](ingestion/), pinned snapshot `data/raw/aact/2026-06-05/`, calibration **40/40 PASS**;
  AACT is never reached at runtime.
- **[built]** Models — real-registry baselines, the synthetic **calibrated** sequence LSTM
  (`sequence_v1.1:demo`), and the honest **negative** survival result (C-index 0.40) —
  [models/](models/), consolidated in [data/models/PHASE3_CARD.md](data/models/PHASE3_CARD.md).
- **[built]** Agentic RAG — hand-rolled LLM-classify router + three scope-bound agents on a shared
  grounding spine, scope-resolved tools (champion-only risk facts + RLS-scoped pgvector search),
  eval-set CI gate — [vigil/agents/](vigil/agents/) (NOT LangGraph / NOT the MCP protocol).
- **[built]** Multi-tenant isolation — Postgres **RLS** on `sponsor_id` + the **SEC-1** cross-site
  service-layer narrowing ([vigil/services/scope_filter.py](vigil/services/scope_filter.py)); the
  sacred cross-tenant + cross-site leakage tests gate every release.
- **[built]** Observability — `message_events`, inspect API + admin page, real cost/latency capture,
  honest-empty `/drift` read surface, Langfuse per-turn tracing (CI-hermetic) — see
  [specs/observability.md](specs/observability.md).
- **[built]** Guide isolation — **layer 1** (static import-graph/config/tool-surface) + **layer 2**
  (behavioral red-team, zero-egress) in CI, **and the now-PROVEN layer 3** (kind + Calico
  NetworkPolicy denial): hand-verified on a real cluster with the PASS transcript committed at
  [deploy/k8s/last-proof-transcript.txt](deploy/k8s/last-proof-transcript.txt).
- **[built]** Clinical-ops loop (Phase 9) — serious-risk crossing → scope-bound at-risk surface
  (real model-attribution reasons + recommended actions) → PII-free scope-bound email doorbell;
  documented driver at [scripts/demo_clinical_ops_loop.py](scripts/demo_clinical_ops_loop.py).
- **[built]** `coded_ref` as the primary participant id + dual-form assistant resolution
  (commit `2a8769b`).
- **[built]** `cohort_at_risk` scoped triage tool (commit `723d0a3`).
- **[built]** Frontend fully API-wired — Wire-1/2/3 + CLEAN-1; `frontend/lib/stubs.ts` is **deleted**;
  honest loading/empty/refusal/error states throughout [frontend/](frontend/).
- **[built]** Containerized API + worker (Gate D1) + a reproducible **live LLM** path keyed from the
  persistent Vault (Gate L1) — [docker-compose.dev.yml](docker-compose.dev.yml) +
  [docker-compose.live.yml](docker-compose.live.yml).
- **[built]** Drift detection (Gate M1) — real **PSI + KS** over the champion prediction distribution,
  persisted to `drift_metric`, served at `GET /monitoring/drift` (scheduled + manual trigger) —
  [models/drift.py](models/drift.py), [specs/observability.md](specs/observability.md).
- **[built]** Model registry + governed-promotion **mechanism** (Gate M3) — `model_registry` catalog
  (migration `0013_model_registry`), `POST /monitoring/models/register` + `…/promote`, an **audited
  champion swap** that retains the prior champion (reversible) and that **champion-only surfacing
  tracks**, honesty hooks (synthetic eval → `architecture_validation`, non-null `model_card_ref`) —
  [vigil/services/routing_service.py:196](vigil/services/routing_service.py#L196),
  [specs/routing.md](specs/routing.md) § Model registry. **Tested at the service + HTTP level**
  (register→promote→audited swap→`GET /monitoring/models` reflects it→rollback) in
  [tests/spine/test_m3_model_registry.py](tests/spine/test_m3_model_registry.py).
  **⚠️ Caveat — mechanism only, NOT demo-verified end-to-end:** the test promotes registry pointers
  whose `.pt` does not exist (`seq_v2_demo.pt` is absent) and never scores a cohort with the promoted
  model; the real demo challenger `sequence_v1.2:demo` is a **registry-only placeholder with no trained
  `.pt`** (see § Scaffolded) and **drift→promote auto-delivery is unwired** — so no model has become
  champion *and then actually scored participants* through this path.
- **[built]** Participant intake front door (INTAKE-1) — a scope-safe `POST /participants` that creates a
  participant + a synthetic visit trajectory WITHIN the caller's scope (`sponsor_id` derived **server-side**
  from the scoped trial, RLS `WITH CHECK`, the SEC-1 site axis, **site-roles-only**) and **auto-enqueues a
  champion score** so a new participant appears scored without a manual batch trigger —
  [vigil/api/routers/participants.py](vigil/api/routers/participants.py),
  [vigil/services/participant_service.py](vigil/services/participant_service.py). Cross-tenant create is
  impossible (proven by the cross-tenant-create-403 test, [tests/spine/test_intake.py](tests/spine/test_intake.py)).
- **[built]** Seed idempotency (SEED-GUARD) — re-running `vigil.seed` on an already-seeded DB now **refuses
  by default** (`SeedExistsError` → clean skip, exit 0) instead of APPENDING a second cohort (the old
  36 → 72 → 112 bloat); `--force` does an FK-safe wipe + reseed — [vigil/seed.py](vigil/seed.py),
  [tests/spine/test_seed_guard.py](tests/spine/test_seed_guard.py). *Optional future nicety: more granular
  partial/corrupt-state detection (today the "already seeded" signal is "the demo sponsors exist").*
- **[built]** Fresh-DB bootstrap (FIX-BOOTSTRAP) — `scripts/bootstrap_db.sql` created `vigil_app` with
  `:'app_password'` **inside a `DO $$` block**, where psql never substitutes the variable → a parse error
  on a truly fresh DB (it only ever "worked" because the role pre-existed cluster-wide). Replaced with the
  literal dev password; the role now bootstraps cleanly + **idempotently** on a fresh cluster, with
  **`NOBYPASSRLS` preserved** (the tenancy guarantee) — [scripts/bootstrap_db.sql](scripts/bootstrap_db.sql).
- **[built]** Postgres durability (HARDEN-1) — added the `pgdata` named volume; seeded data now **persists
  across restarts/recreates** (`docker compose down -v` still intentionally wipes) —
  [docker-compose.dev.yml](docker-compose.dev.yml).

## 2. SCAFFOLDED / partial — built, but NOT complete

> These have a real foundation in the tree. They are **not** finished; do not read them as done.

- **[scaffolded]** **Challenger model** — `sequence_v1.2:demo` is registered in `routing_state` +
  `model_registry` for the governance demo, but there is **no trained artifact** (only
  `data/models/t2d/sequence_v1.1_demo.pt` exists; no `sequence_v1.2_demo.pt`). It is a
  registry-only placeholder ([vigil/seed.py](vigil/seed.py#L622)) — a real challenger needs a trained
  `.pt` + an honest eval.
- **[scaffolded]** **Drift → routing closed loop** — the *ends* exist but the *link* does not:
  M1 computes + stores real PSI/KS, and `routing_service.handle_breach(BreachSignal)` consumes an
  opaque breach signal and performs the fallback transition
  ([vigil/services/routing_service.py](vigil/services/routing_service.py#L129)). What is **NOT** built:
  the **auto-delivery** of a detected breach *into* `handle_breach` (the delivery mechanism is still an
  open question in [specs/routing.md](specs/routing.md) § Reaction interface) and **M2** (alerting the
  ML engineer on a breach). M3 (registry + governed promote) is built; the *automatic* trigger that
  closes drift→alert→promote is not.
- **[scaffolded]** **Regime threading** — the champion resolver itself is built
  (`_resolve_champion_version(regime)` → `routing_state`,
  [vigil/workers/tasks.py](vigil/workers/tasks.py#L355)), but it is not reachable from real callers:
  `ScoringTriggerIn` carries **no `regime` field**
  ([vigil/api/routers/scoring.py:28](vigil/api/routers/scoring.py#L28)) and `trigger_scoring` drops it,
  so the resolver's production hard-raise is unreachable and demo mode falls back to `regime='t2d'`.
  Related: there is **no indication column on the operational `trial` table** (regime must derive from
  a persisted source — `ref_trial.therapeutic_area` exists in ingestion but is not written to the
  operational `trial`), and the second-regime (`alz`) seed for end-to-end multi-regime dispatch is not
  built (`ROADMAP.md` Phase-4 register).
- **[scaffolded]** **Automated scoring pipeline** — the **primitives all exist**: the Arq worker +
  `score_trial` ([vigil/workers/tasks.py:486](vigil/workers/tasks.py#L486)),
  `scoring_service.trigger_scoring` ([vigil/services/scoring_service.py](vigil/services/scoring_service.py)),
  the worker **cron** mechanism `compute_drift` already uses ([vigil/workers/settings.py](vigil/workers/settings.py)),
  and the idempotent **crossing-detection → PII-free alert** path; and **INTAKE-1** auto-enqueues a score on
  create. **NOT built — the automatic enqueueing:** (a) a **scheduled nightly batch re-score** of all active
  participants (wrap `trigger_scoring` in a `WorkerSettings.cron_jobs` entry, mirroring `compute_drift`);
  (b) **event-triggered re-scoring** on an engagement change (a logged missed visit → enqueue a score → the
  existing crossing-detection + alert). Batch for completeness, events for urgency. Note:
  **single-participant scoring does not yet exist** (`score_trial` is whole-trial only) — that's the unit to
  add first.

## 3. DEFERRED & why — genuine future phases

- **[deferred]** **Full production K8s for the real app** — HPA on `api`/`worker`, app-side egress
  NetworkPolicies, prod Deployments/Services/Ingress. `deploy/k8s/` today carries **only** the Guide
  layer-3 isolation proof (kind + Calico); the rest of Phase 8 prod infra is future work.
- **[deferred]** **Production secrets — auth method + KMS auto-unseal.** Today the app authenticates to
  Vault with a **STATIC scoped read token** (`VAULT_TOKEN` in `.env.demo` locally — the dev stand-in for
  production auth), and the persistent Vault uses **manual Shamir 3-of-5 unseal**. Production hardening:
  replace the static token with a **Vault auth method** (AppRole / Kubernetes service-account / cloud IAM)
  so the app holds **no static token**, and use **cloud-KMS / transit auto-unseal** (no human keys) —
  eliminating static secrets outside Vault. Both are a **config swap, not an architecture change**
  (non-root + persistent file storage + sealed-at-rest are identical). Not wired here (no KMS available).
  **By design — NOT a gap:** the public **Guide keeps its OWN LLM key in an env var**. It is
  network-isolated (`guide-net`), has **no Vault client**, and no path to the app/DB — so vaulting the
  Guide's key would *breach* that isolation. The Guide's env-key is intentional, not a secrets-management
  oversight.
- **[deferred]** **Dev-vault profile separation** — the principled follow-up from the two-vault
  investigation. `demo-up.ps1` (HARDEN-1) **names the services** so exactly ONE vault — the persistent one
  — runs on the live path; the cleaner structural version moves `vault-dev` + `vault-seed` into their own
  compose profile so *which vault runs* is a property of the **activated profiles**, not a service-naming
  workaround. Touches the base `depends_on` wiring → needs a dev-path test before shipping.
- **[deferred]** **Guide pgvector parity** — the Guide reads its **own file-backed** approved-doc index
  by design (strongest isolation); migrating it to a pgvector store at parity with the app is deferred
  to Phase 8 ([specs/isolation.md](specs/isolation.md) § Phase 7 ratified decisions).
- **[deferred]** **Sponsor SOP / protocol RAG collections** — `document_chunk` RLS is already shaped
  for sponsor-scoped docs (`sponsor_id` nullable; global vs sponsor policy), but the Phase-5 corpus is
  **global model-cards only**; sponsor-scoped protocol/SOP ingestion is deferred.
- **[deferred]** **Auth hardening** — refresh-token rotation (`/auth/refresh` is `TODO(wire-later)`;
  stale token → re-login today) and explicit rate-limit verification.
- **[deferred]** **Real-IPD / clinical validation** — **OUT OF SCOPE BY DESIGN.** Vigil proves
  *method and architecture* on a clearly-labelled synthetic cohort; it is never a validated clinical
  tool and must not be presented as one. Validating on real individual patient data is a different
  project with its own regulatory/ethics surface.

## 4. Known minor issues & polish

> Recorded honestly from the behavioral bug-hunt — none alarming, all deliberately deferred. These are
> nits / UX, **not** correctness, tenancy, or isolation defects.

- **Cohort-triage intent match is narrow** — some natural phrasings ("which of my participants are at
  risk") fall through to a generic *"I don't have grounded information"* instead of routing to the
  `cohort_at_risk` tool or answering *"no at-risk participants in your scope"*. Broaden the intent match /
  improve the empty-scope message ([vigil/agents/agent_base.py](vigil/agents/agent_base.py)).
- **UUID shown to non-identity roles in ASSISTANT answers** — when no `coded_ref` is surfaced the
  assistant falls back to the internal UUID
  ([vigil/agents/agent_base.py:116](vigil/agents/agent_base.py#L116)). It is the caller's OWN-scope id (not
  a cross-tenant leak) but contradicts the coded-ref-everywhere intent.
- **Empty / whitespace assistant message accepted** → a billable router turn (`AssistantMessageIn.content`
  has `max_length` but **no `min_length`** — [vigil/api/routers/assistant.py:29](vigil/api/routers/assistant.py#L29)).
- **Admin malformed-UUID body → 500** on the admin-only write path (should be a 422).
- **Render polish** — the loading risk dial shows `0`; a blank 7-day-trend column; a single risk-history
  point renders as a lone dot; a traffic-split divide-by-zero is latent (currently shielded).
- **Participant "explanation" is a client-side template** under a *"generated by the model"* footer —
  reword to *"assembled from the model's outputs"* OR wire `/assistant` for a true generated explanation.
- **Guide cache hit logs $0 / 0-token** → slightly **understates** cumulative Guide cost on the LLMOps card.
- **`datetime.utcnow()` deprecation** ([vigil/services/scoring_service.py:123](vigil/services/scoring_service.py#L123)).
- **`demo_secret` hardcoded** ([vigil/core/config.py:67](vigil/core/config.py#L67)) — safe only because
  `demo_mode` defaults off (it gates `POST /scoring/inject_events`).
- **No refresh-token rotation** — `/auth/refresh` is deferred (see *Auth hardening* in § 3); a 30-min JWT
  TTL means re-login on expiry.
