# Vigil — Roadmap & Progress

Source of truth for *where we are*. The `release` gate proves a phase **passes**;
this file tracks what is **done** and **pending**. Update via the `progress` skill.

Legend: [x] done · [~] in progress / pending review · [ ] not started

---

## Phase 0 — Specs & Claude/agent setup  [x]
- [x] Seven specs filled, conformance green
- [x] Skills (data-cleaning, schema-migration, spec-conformance) + 4 subagents committed (5 with `eda`, added in Phase 1)
- [x] uv toolchain + CI green on GitHub
**Done when:** specs reviewed; `check_specs.py` green. ✅

## Phase 1 — Data: fetch, clean, synthesize  [x]
- [x] Pipeline runs end-to-end on REAL AACT: raw → clean → synthetic + quality + calibration reports (`make data`)
- [x] **Ran `--live` against a real AACT snapshot** (2026-06-05, hosted AACT): raw+manifest pinned under `data/raw/aact/2026-06-05/`; cleaned to ref_* (73,073 trials / 182,240 arms / 384,802 reasons), 760 fail-loud rows recorded
- [x] **EDA on real `ref_*` data** → `data/eda/` (overall dropout trial-mean 0.202 / participant-weighted 0.151; by phase/area/enrollment; reason mix; missingness; covariate↔dropout signs as expected)
  - viewable interactively: `make eda` → marimo notebook (`ingestion/eda/notebook.py`); generator is now `make eda-report`. Read-only `eda` subagent (`.claude/agents/eda.md`) answers data questions over the snapshot.
- [x] **SPEC RATIFIED to CTGOV2** (commit `857d505`): `specs/data.md` adopts CTGOV2 enum codes (+`ECT`); `schema.py`/`ctgov_enums.py`/`clean.py`/`vocab.py`/fixture reconciled. Quality report now 0 spec-contradictions / 0 unmapped-enums.
- [x] **Calibration targets populated from REAL EDA** (`targets.py`, provenance snapshot 2026-06-05): overall + per-stratum dropout (phase/area/sponsor_class/blinding/site-count), reason-mix (STUDY_TERMINATED/ADMINISTRATIVE incl., censoring excluded), enrollment+arm marginals, covariate-sign checks. `early_fraction` stays a labelled ASSUMPTION (AACT has no per-event timing — TODO `targets.py:250`).
- [x] **Synthetic cohort regenerated + calibrated** — deterministic (seed in manifest, bit-identical), `synthetic=True` on all 96,684 rows, SYNTHETIC README intact. **Calibration report 40/40 PASS** within spec tolerances (overall trial-mean 0.2016 vs 0.2018 ±1pp; all per-stratum ±3pp; reason-mix χ² p=0.58; timing assumption ±10pp; covariate signs matched; marginals matched), fails loud on any miss.
**Done when:** one command runs raw → clean → synthetic from REAL data, quality + calibration reports pass, validates against data spec. ✅
> DONE. Calibrated to the **trial-mean** overall (0.2018 — matches spec's "mean not_completed/started"); participant-weighted 0.151 recorded alongside (the two aggregations can't both be hit at the marginal level). Synthetic is generated from the real ref_* snapshot (deterministic stratified subsample ~8k trials → 96,684 participants), each trial at its own real rate. Engagement series capped 90d (new deterministic baseline). early_fraction remains a labelled assumption.

## Phase 2 — Skeleton: secrets, DB, auth, sessions, queue  [x]
- [x] Vault → Postgres+RLS → auth → scoped layer → Redis → Arq → seed → scoped user creation
- [x] Leakage test 7/7 (data isolation + user-creation subset), VERIFIED on real Postgres as NOBYPASSRLS `vigil_app`
- [x] Review pass (2026-06-05): RLS policies, JWT claim shape, scoped-creation match `domain.md`; job round-trip green
- [x] All 7 roles login + `/me` (platform admin, auditor, sponsor oversight, coordinator, PI, study manager, CRA) — `test_all_seven_roles_login_and_me`; CRA + PI now seeded
- [x] CI runs the fast suite (`pytest -m "not slow"`) so Phase-1 ingestion tests execute in CI, plus leakage + spine
**Done when:** login per role + both sponsors; leakage test passes (data + user creation); job round-trips. ✅
> Verified on real Postgres+Redis (full suite 45 passed, leakage 7/7).

## Phase 3 — Models  [ ]
Baselines (logistic + GBT) on real registry · sequence model (synthetic cohort) · survival ·
calibration + SHAP · temporal-only eval (PR-AUC, recall@precision, lead-time gain) · scores → Postgres.
**Done when:** reproducible training from Phase 1 REAL data; metrics logged; scores behind RLS.
> Blocked on Phase 1 real data + EDA.

## Phase 4 — Console, API & model routing  [~]
FastAPI per api spec · four screens (v0) via scoped layer · routing (regime, champion/challenger, drift-fallback, audited promotion).
- [~] Frontend v0 scaffolded (`frontend/`): four screens + login + docked assistant, shared `AppNav`/`AppChrome` (assistant gated off `/login`), fake auth context. **All data STUBBED** in `lib/stubs.ts`, typed to `specs/api.md` schemas (`lib/types.ts`). No backend, no real auth, no model routing.
- [ ] FastAPI endpoints per `specs/api.md`; wire each stub boundary to the scoped data layer (see frontend register below)
- [ ] Model routing (regime, champion/challenger, drift-fallback, audited promotion)
**Done when:** coordinator sees only scoped cohort; routing decisions audited.
> Frontend is stubs-only (typed to api.md). Stays [~] until the API exists and the boundaries below are wired through the scoped layer.

## Phase 5 — Agentic layer & RAG (local)  [ ]
Retention/Report/Operations agents via MCP in caller scope · hybrid RAG (structured + pgvector) ·
LangGraph + Langfuse · queued with caps + jittered retries + cost tracking · guardrails + PII redaction.
**Done when:** in-scope answered with citations; clinical/out-of-tenant refused.

## Phase 6 — Observability (both surfaces)  [ ]
`message_events` + structured logs · Langfuse · admin page (inspect/verify-guardrails/debug-retrieval/show-redaction) · wire monitoring + cost screens.
**Done when:** every message → event row; guardrails + retrieval inspectable; redaction verifiable.
> NOT STARTED. `message_events` has no implementation and has never been run — no table writes, no Langfuse, no admin page. The Phase-4 monitoring/cost screens are stubbed UI only (no event data behind them).

## Phase 7 — Public Guide demo (isolated)  [ ]
Separate service, own creds · landing/demo site · Guide RAG over approved docs only · guardrails · emits message_events.
**Done when:** answers from approved docs; refuses out-of-scope; proven unable to reach real resources even when prompted.

## Phase 8 — Production-readiness (no paid hosting)  [ ]
Docker Compose dev · deploy/k8s (Deployments/Services/Ingress/Config/Secrets/Jobs/CronJobs/NetworkPolicies/HPA) on kind/minikube.
**Done when:** comes up on kind; CronJobs run; NetworkPolicies block the Guide.

---

## Open TODO register
Synced from `TODO(...)` comments in the tree + decisions. (file:line · note)
- [x] extract.py:153 — RESOLVED 2026-06-05: `--live` run against hosted AACT, snapshot pinned (TODO removed)
- [ ] clean.py:76 — never let sample fixtures be mistaken for real AACT
- [ ] targets.py:250 — revisit early_fraction once a real per-event timing source exists (still a labelled ASSUMPTION)
- [x] **EDA-before-cohort**: real extract → EDA → targets → synth (Phase 1 reorder) — COMPLETE; synthetic calibrated 40/40 PASS
- [x] **SPEC FIX (main):** CTGOV2 enum format ratified in `specs/data.md` (commit `857d505`); `ctgov_enums.py` normalization kept; `ECT` added as a `primary_purpose`. Code reconciliation committed (`980162d`).
- [x] **Withdrawal-reason vocab**: SET expanded (commit `2b4d554`) — added `STUDY_TERMINATED` (12.2%) + `ADMINISTRATIVE` (8.2%); censoring/ongoing now excluded from the mix (183,815 vol / 2,490 rows recorded separately, never counted as dropout). `OTHER` 50.7% → **17.54%** (honest floor; arm-level dropout unchanged, trial-mean 0.2018). `vocab.py`/`clean.py`/`report.py` reconciliation committed (`980162d`).
- [x] Sample fixture: gitignored (`ingestion/fixtures/aact_sample/`); conftest builds a temp sample (`tmp_path_factory`) so tests/CI don't need the file

### Frontend wiring register (Phase 4/5) — endpoint → screen/boundary
Stub data layer is `frontend/lib/stubs.ts` (typed to `specs/api.md` via `frontend/lib/types.ts`). Each `// TODO(phase4/5)` below is unwired.
- [ ] `POST /auth/login` + `GET /auth/me` → login + auth context (`app/login/page.tsx`, `lib/auth-context.tsx:26,32`, `lib/stubs.ts:26`)
- [ ] `GET /cohort` + `GET /cohort/summary` → Dashboard + Triage (`app/page.tsx:39`, `app/triage/page.tsx:45`, `lib/stubs.ts:93,98`)
- [ ] `GET /participants/{id}` + `/risk` → Participant detail (`app/participant/[id]/page.tsx:33,35`, `lib/stubs.ts:108,121`)
- [ ] `POST /participants/{id}/interventions` → triage Call + detail "Schedule Intervention" (`app/triage/page.tsx:63`, `app/participant/[id]/page.tsx:54`, `lib/stubs.ts:138`)
- [ ] `GET /monitoring/models` + `/drift` → Monitoring (`app/monitoring/page.tsx:28,30`, `lib/stubs.ts:154,185`)
- [ ] `GET /monitoring/cost` (+`/models`) → Cost & Routing — CostReport schema not yet in api.md (`app/costs/page.tsx:10`)
- [ ] `POST /assistant/conversations` + `/messages` (202) + poll `GET /assistant/jobs/{id}` → docked assistant (async flow stubbed) (`components/vigil/assistant-panel.tsx:209,216,219`, `lib/stubs.ts:225,230,243`)
- [ ] No-endpoint gaps to flag at wire time: per-participant risk history (sparkline/trend), `daysEnrolled` in cohort rows, assistant-generated prose for the explanation card — none have an api.md field (`app/page.tsx:27,29`, `app/triage/page.tsx:31,33`, `app/participant/[id]/page.tsx:59`)

### Process / housekeeping TODOs
- [ ] **Add frontend lint/typecheck to CI at Phase 4** — `frontend/` is not in CI; add `tsc --noEmit` (+ eslint) once the API/build lands. Note: `next.config.mjs` has `typescript.ignoreBuildErrors: true`, so `next build` will not catch type errors — CI must run `tsc` explicitly.
- [ ] **Group tests by phase** — reorganize into `tests/ingestion`, `tests/spine`, `tests/models` (markers/paths) so phase suites run independently.
- [ ] **Remove unused frontend files if any remain** — currently `frontend/components/vigil/header.tsx` and `participant-table.tsx` are superseded by `AppNav` / `TriageTable` and unused; delete or repurpose.

ROADMAP TODO (RAG stage): build the public "receptionist" RAG agent over real analyzed studies — name, sponsor/industry (class + name), results/findings, study_url link, searchable by condition/phase/sponsor. Behaviour: on broad query (e.g. "oncology studies") either return a list or ask 1–3 narrowing questions (phase, sponsor type, status). Requires re-extract of AACT text + sponsor name into a ref_corpus (NOT modelling features, NOT tenant data). Isolated from app DB. Spec in data.md + rag.md at that stage.
## Decisions log (settled)
- Sponsor = hard tenant boundary (RLS). CRO scoped per assignment.
- ClinicalTrials.gov/AACT = build-time only; synthetic cohort proves method validity, not clinical prediction.
- pgvector (not Pinecone); LangGraph orchestration; Langfuse tracing.
- Two surfaces; public Guide isolated in three layers.
- uv toolchain; release agent prepares commits; CI runs the same checks.

