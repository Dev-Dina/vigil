# Vigil — Roadmap & Progress

Source of truth for *where we are*. The `release` gate proves a phase **passes**;
this file tracks what is **done** and **pending**. Update via the `progress` skill.

Legend: [x] done · [~] in progress / pending review · [ ] not started

---

## Phase 0 — Specs & Claude/agent setup  [x]
- [x] Seven specs filled, conformance green
- [x] Skills (data-cleaning, schema-migration, spec-conformance) + 4 subagents committed
- [x] uv toolchain + CI green on GitHub
**Done when:** specs reviewed; `check_specs.py` green. ✅

## Phase 1 — Data: fetch, clean, synthesize  [~]
- [~] Pipeline built and green **on the SAMPLE fixture only** (dry run on synthetic data)
- [ ] **Run `--live` against a real AACT snapshot** (gate — see TODO extract.py:153)
- [ ] **EDA on real `ref_*` data** (study count after filter; dropout/withdrawal/enrollment distributions; missingness; covariate–dropout association)
- [ ] **Derive calibration targets from real EDA** (replace assumed values, e.g. early_fraction)
- [ ] Regenerate synthetic cohort calibrated to the REAL targets
- [ ] Calibration report compares synthetic ↔ real targets
**Done when:** one command runs raw → clean → synthetic from REAL data, quality + calibration reports pass, validates against data spec.
> Note: current "done" is a dry run on the fake fixture. Not real until the live + EDA steps land.

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

## Phase 4 — Console, API & model routing  [ ]
FastAPI per api spec · four screens (v0) via scoped layer · routing (regime, champion/challenger, drift-fallback, audited promotion).
**Done when:** coordinator sees only scoped cohort; routing decisions audited.

## Phase 5 — Agentic layer & RAG (local)  [ ]
Retention/Report/Operations agents via MCP in caller scope · hybrid RAG (structured + pgvector) ·
LangGraph + Langfuse · queued with caps + jittered retries + cost tracking · guardrails + PII redaction.
**Done when:** in-scope answered with citations; clinical/out-of-tenant refused.

## Phase 6 — Observability (both surfaces)  [ ]
`message_events` + structured logs · Langfuse · admin page (inspect/verify-guardrails/debug-retrieval/show-redaction) · wire monitoring + cost screens.
**Done when:** every message → event row; guardrails + retrieval inspectable; redaction verifiable.

## Phase 7 — Public Guide demo (isolated)  [ ]
Separate service, own creds · landing/demo site · Guide RAG over approved docs only · guardrails · emits message_events.
**Done when:** answers from approved docs; refuses out-of-scope; proven unable to reach real resources even when prompted.

## Phase 8 — Production-readiness (no paid hosting)  [ ]
Docker Compose dev · deploy/k8s (Deployments/Services/Ingress/Config/Secrets/Jobs/CronJobs/NetworkPolicies/HPA) on kind/minikube.
**Done when:** comes up on kind; CronJobs run; NetworkPolicies block the Guide.

---

## Open TODO register
Synced from `TODO(...)` comments in the tree + decisions. (file:line · note)
- [ ] extract.py:153 — validate `--live` against a real AACT snapshot (Phase 1 gate)
- [ ] clean.py:75 — never let sample fixtures be mistaken for real AACT
- [ ] targets.py:113 — revisit early_fraction once real per-event timing exists
- [ ] **EDA-before-cohort**: real extract → EDA → targets → synth (Phase 1 reorder)
- [x] Sample fixture: gitignored (`ingestion/fixtures/aact_sample/`); conftest builds a temp sample (`tmp_path_factory`) so tests/CI don't need the file

## Decisions log (settled)
- Sponsor = hard tenant boundary (RLS). CRO scoped per assignment.
- ClinicalTrials.gov/AACT = build-time only; synthetic cohort proves method validity, not clinical prediction.
- pgvector (not Pinecone); LangGraph orchestration; Langfuse tracing.
- Two surfaces; public Guide isolated in three layers.
- uv toolchain; release agent prepares commits; CI runs the same checks.
