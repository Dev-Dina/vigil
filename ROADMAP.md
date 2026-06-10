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
- [x] **Step (c) survival model** — discrete-time hazard on censored T2D synthetic cohort. C-index=0.3998 (concordance_index(tte, -H, event); see note below). `models/t2d/survival.py`; artifacts in `data/models/t2d/`; tests green. Censoring ~0.03% (non-informative admin cutoff, documented — not the clinical value driver).
- [x] **Phase 3 consolidated scorecard written** — `data/models/PHASE3_CARD.md` (2026-06-09): all numbers verified against source JSONs; negative survival result stated plainly; provenance ladder documented; central thesis + what-this-does-not-prove sections included.
> Phase 1 REAL data + EDA are DONE — Phase 3 is no longer blocked on them. Now blocked on two
> pre-conditions: (1) the **feature-contract spec** is not yet ratified into `specs/data.md`
> (scoped modelling cohort {PHASE1/PHASE2, PHASE2, PHASE2/PHASE3, PHASE3}; sponsor_class is a
> feature / identity is NOT; max_age_years missingness as boolean, not imputed) — per the
> per-phase ritual, spec on `main` first; (2) two open scoring decisions: (a) scoring-target
> mismatch — how `risk_score` reaches operational/demo participants with no engagement series;
> (b) baseline label definition (binary dropout-rate cut vs continuous vs overall_status/
> why_stopped — the latter not in the cleaned schema). Held-out split (group-disjoint by
> `nct_id`) is the Phase-3 test artifact (golden = transforms only).

## Phase 4 — Console, API & model routing  [~]
FastAPI per api spec · four screens (v0) via scoped layer · routing (regime, champion/challenger, drift-fallback, audited promotion).
- [~] Frontend v0 scaffolded (`frontend/`): four screens + login + docked assistant, shared `AppNav`/`AppChrome` (assistant gated off `/login`), fake auth context. **All data STUBBED** in `lib/stubs.ts`, typed to `specs/api.md` schemas (`lib/types.ts`). No backend, no real auth, no model routing.
- [ ] FastAPI endpoints per `specs/api.md`; wire each stub boundary to the scoped data layer (see frontend register below)
- [x] specs/routing.md ratified on main (routing spec-complete; BUILD pending — see line below)
- [ ] Model routing (regime, champion/challenger, drift-fallback, audited promotion)
  - [x] B1 built: routing_state migration + ORM, champion resolver wired in score_trial, t2d seed row
  - [x] **B2a-spec done**: engagement (visit trajectory) table spec ratified in `specs/scoring.md § Engagement (visit trajectory) input`; cross-referenced from `specs/data.md`; `check_specs.py` updated (11 required sections). Root cause for the block: the sequence LSTM was found unscoreable in B2 because no trajectory data existed in the operational DB — no engagement table, no static covariates on `participant`. The gate reframes subsequent work as:
  - [x] **B2a-spec-2 done** (corrected): covariate-provenance invariant (#10) ratified; imputed-capable participant covariates are exactly `age_years`, `hba1c_pct`, `bmi` (3 columns, each with `*_baseline_imputed` companion). Decision c: `planned_duration_days` is trial-level (read from trial record at feature-assembly time), NOT a participant column, carries no flag.
  - [x] **B2a-1 done**: engagement migration (0004) + RLS (FORCE, fail-closed, no platform bypass) + participant covariate columns (`age_years/hba1c_pct/bmi` + 3 `*_baseline_imputed` flags + `sex`) + `TENANT_TABLES` updated + `EXCLUDED_FROM_FEATURES` extended with 3 flags + invariants 6/7/10 tested. Invariants 8/9 deferred to B2b (require running scorer).
    - [x] **B2a-2 done**: seed bridge — synthetic T2D engagement seeded to demo participants (2026-06-10). Mapping: `synthetic_pid = uint32(MD5(uuid.bytes + b"seed-bridge-v1")[:4]) % n_participants` (deterministic, versioned, auditable). Reference epoch 2023-01-01 UTC. miss_probability withheld (inv 4). Covariates + *_baseline_imputed flags set from parquet provenance. 7 spine tests green. **B2b (LSTM artifact + _load_scorer wiring) is now unblocked.**
    - [x] **B2b done** (2026-06-11): real sequence LSTM now scores live from engagement — live path no longer random. `data/models/t2d/sequence_v1.0_demo.pt` persisted (test PR-AUC=0.3390, pre-registered bar 0.3006 PASSED). `LSTMScorer` wired as champion; `_demo_scorer` (rng) retained as explicit no-artifact fallback only. Trial context columns added (migration 0005: `trial.n_sites`, `trial.planned_duration_days`, `trial.phase`; `participant.arm_type`). Temporal guard + feature leakage guards wired in all paths. Invariants 8 (synthetic propagation) and 9 (single `_seq_feature_frame` builder) satisfied. 7 spine slow tests green (fidelity, scoreability, inv 8, inv 9, inv 7 live, artifact exists, LSTMScorer loaded). **Live escalation demo (inject worsening engagement → risk rises → climbs watchlist → alert) is NOW honestly claimable.** B2c (GBT shadow) and B3 (fallback/promotion) remain.
    - [ ] **B2c**: GBT structural shadow champion (second model version, non-trajectory; regime routing end-to-end)
  - [ ] **B2 dependency — regime not threaded**: `ScoringTriggerIn` has no `regime` field; `trigger_scoring` drops it; resolver hard-raise is unreachable from real API callers until wired through the trigger path
  - [ ] **B2 dependency — no indication column on `trial`**: operational `trial` table has no `therapeutic_area`/`regime` column; regime must derive from a persisted source (`ref_trial.therapeutic_area` exists in AACT ingestion but was never written to the operational `trial` table — requires migration + backfill decision)
  - [ ] **B2 seed — ALZ champion**: add a second regime (`alz`) seeded with the structural-only champion (real decomposition, PR-AUC 0.77, NOT a trajectory/synthetic model) to prove multi-regime dispatch end-to-end
- [x] scoring writeback: `participant_score` behind RLS (no platform bypass), Alembic migration 0002, `ParticipantScore` ORM in `TENANT_TABLES`, `POST /scoring/trigger` (202), `GET /scoring/jobs/{job_id}`, `POST /scoring/inject_events` (DEMO_MODE gated), Arq `score_trial` worker with jitter + `assert_no_outcome_features` + `run_smoke` guards, seed writes demo score rows, 5 isolation invariants in `tests/spine/test_scoring_leakage.py`
- [~] Dashboard data layer wired: real API client (lib/api.ts) replacing stubs; synthetic badge on watchlist + banner on participant panel; role-gate for ml_admin/auditor; demo loop (inject→poll→refresh→alert); participants router stub with correct 403 guards; synthetic added to CohortRow (api.md + cohort router + types.ts). STOP — user reviews before commit.
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
- [x] clean.py:76 — RESOLVED: fabricated sample fixture retired; the only non-live substrate is now the REAL-AACT golden set (`tests/golden/`), so a sample can no longer be mistaken for real (TODO removed)
- [ ] targets.py:250 — revisit early_fraction once a real per-event timing source exists (still a labelled ASSUMPTION)
- [x] **EDA-before-cohort**: real extract → EDA → targets → synth (Phase 1 reorder) — COMPLETE; synthetic calibrated 40/40 PASS
- [x] **SPEC FIX (main):** CTGOV2 enum format ratified in `specs/data.md` (commit `857d505`); `ctgov_enums.py` normalization kept; `ECT` added as a `primary_purpose`. Code reconciliation committed (`980162d`).
- [x] **Withdrawal-reason vocab**: SET expanded (commit `2b4d554`) — added `STUDY_TERMINATED` (12.2%) + `ADMINISTRATIVE` (8.2%); censoring/ongoing now excluded from the mix (183,815 vol / 2,490 rows recorded separately, never counted as dropout). `OTHER` 50.7% → **17.54%** (honest floor; arm-level dropout unchanged, trial-mean 0.2018). `vocab.py`/`clean.py`/`report.py` reconciliation committed (`980162d`).
- [x] **Golden set replaces the fabricated fixture**: the deterministically-generated SAMPLE fixture (`ingestion/fixtures/`, `build_sample.py`) is **retired**. The ingestion clean-transform oracle is now `tests/golden/` — a frozen slice of REAL public AACT (snapshot 2026-06-05, 64 NCTs across every phase×sponsor_class×has_withdrawal×has_max_age stratum) with committed `raw/` + `expected/` CSVs + `selection.json` + `build_golden.py` + README. NO PHI, NO synthetic. `clean_snapshot(raw)` reproduces `expected/` via `assert_frame_equal` (`tests/test_golden_oracle.py`). The fast suite + the non-live pipeline run off it (`make golden`).
- [x] **Report-clobber guard (Gate 2 fix)**: a non-live run can no longer overwrite `data/reports` (defaults to `data/reports_fixture`, fails loud on an explicit `REPORT_ROOT` override — same pattern that protects `data/clean`). `data/reports/data_quality_report.json` regenerated from the REAL snapshot: ref_trial 73,073 / ref_arm 182,240 / 760 fail-loud drops / 2,490 censoring rows (183,815 vol).
- [x] **miss_probability leakage hole (spec/code drift) closed before B2b** (2026-06-10): `specs/scoring.md` § inv 4 claimed `assert_no_outcome_features` forbids `miss_probability` (the synthetic generator's latent hazard); `models/leakage_check.py::_FORBIDDEN_TOKENS` did not list it. Fixed: `miss_probability`, `time_to_event`, `event_observed`, `arm_real_dropout_rate` added to the shared forbidden set; three new tests in `tests/spine/test_engagement_leakage.py` assert the guard raises loudly. All 129 fast-suite tests green.
- [ ] **CHEAP-FIX (Finding 2)**: pan-indication test base rate / positive prevalence not recorded in `data/models/baselines/metrics.json` — the 0.697 PR-AUC cannot be read against its chance baseline from the artifact alone. Add `n_positives` and `positive_prevalence` to the test block when the baselines script next runs.
- [x] **Finding 3 (survival C-index sign convention) — VERIFIED CORRECT, not a bug**: `concordance_index(tte, -H, event)` in `models/t2d/survival.py:263` is correct; C-index 0.400 is a true negative result (anti-concordant on TTE ranking), not a double-negation artifact. No fix needed; flagged to prevent future re-review.

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
- [ ] **Enforce the evaluation contract in `check_specs.py`** — extend the conformance check as each phase lands: a **models** phase must have a held-out split; a **RAG** phase must have an eval set; a **transform** must have a golden set (per `specs/data.md` "Evaluation contract").
- [ ] **Add frontend lint/typecheck to CI at Phase 4** — `frontend/` is not in CI; add `tsc --noEmit` (+ eslint) once the API/build lands. Note: `next.config.mjs` has `typescript.ignoreBuildErrors: true`, so `next build` will not catch type errors — CI must run `tsc` explicitly.
- [ ] **Group tests by phase** — reorganize into `tests/ingestion`, `tests/spine`, `tests/models` (markers/paths) so phase suites run independently.
- [ ] **Remove unused frontend files if any remain** — currently `frontend/components/vigil/header.tsx` and `participant-table.tsx` are superseded by `AppNav` / `TriageTable` and unused; delete or repurpose.

ROADMAP TODO (RAG stage): build the public "receptionist" RAG agent over real analyzed studies — name, sponsor/industry (class + name), results/findings, study_url link, searchable by condition/phase/sponsor. Behaviour: on broad query (e.g. "oncology studies") either return a list or ask 1–3 narrowing questions (phase, sponsor type, status). Requires re-extract of AACT text + sponsor name into a ref_corpus (NOT modelling features, NOT tenant data). Isolated from app DB. Spec in data.md + rag.md at that stage.

ROADMAP TODO (Phase 6 / demo): live demo script that generates fresh synthetic participants on the fly and feeds them through ingest→score→rank, shown updating on the dashboard. Two modes: (1) in-distribution → demonstrates live scoring; (2) --drift → generator parameters shifted so PSI/KS cross threshold, drift fires on the monitoring screen and triggers audited fallback (Phase 4 routing). Builds on 5a-(i) participants-as-synthetic-members-with-series. Frame to audience as simulated incoming ePRO batches (synthetic, no PHI).
## Decisions log (settled)
- Sponsor = hard tenant boundary (RLS). CRO scoped per assignment.
- ClinicalTrials.gov/AACT = build-time only; synthetic cohort proves method validity, not clinical prediction.
- pgvector (not Pinecone); LangGraph orchestration; Langfuse tracing.
- Two surfaces; public Guide isolated in three layers.
- uv toolchain; release agent prepares commits; CI runs the same checks.
- **Evaluation contract ratified** (`specs/data.md` + CLAUDE.md per-phase ritual): one test artifact per purpose — **golden = transforms, eval set = RAG, held-out split = models**. Choosing the wrong type is a spec violation. `check_specs.py` enforcement tracked in housekeeping TODOs.

