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
- [x] Pipeline built and green (clean + EDA now run on REAL AACT; synthetic still pending real calibration)
- [x] **Ran `--live` against a real AACT snapshot** (2026-06-05, hosted AACT): raw+manifest pinned under `data/raw/aact/2026-06-05/`; cleaned to ref_* (73,073 trials / 182,240 arms / 377,888 reasons), 760 fail-loud rows recorded
- [x] **EDA on real `ref_*` data** → `data/eda/` (overall dropout trial-mean 0.202 / participant-weighted 0.151; by phase/area/enrollment; reason mix; missingness; covariate↔dropout signs as expected)
  - viewable interactively: `make eda` → marimo notebook (`ingestion/eda/notebook.py`); generator is now `make eda-report`. Read-only `eda` subagent (`.claude/agents/eda.md`) answers data questions over the snapshot.
- [x] **SPEC RATIFIED to CTGOV2** (commit `1b65d08`): `specs/data.md` adopts CTGOV2 enum codes (+`ECT`); `schema.py`/`ctgov_enums.py`/`clean.py`/`vocab.py`/fixture reconciled (HELD for review). Quality report now 0 spec-contradictions / 0 unmapped-enums.
- [ ] **Derive calibration targets from real EDA** (replace assumed values, e.g. early_fraction)
- [ ] Regenerate synthetic cohort calibrated to the REAL targets
- [ ] Calibration report compares synthetic ↔ real targets
**Done when:** one command runs raw → clean → synthetic from REAL data, quality + calibration reports pass, validates against data spec.
> Extract + clean + EDA landed on REAL data against a ratified CTGOV2 spec. Stays [~] until real calibration targets + synthetic regen land. Code reconciliation (step 2–3) held for review.

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
- [x] extract.py:153 — RESOLVED 2026-06-05: `--live` run against hosted AACT, snapshot pinned (TODO removed)
- [ ] clean.py:76 — never let sample fixtures be mistaken for real AACT
- [ ] targets.py:113 — revisit early_fraction once real per-event timing exists
- [ ] **EDA-before-cohort**: real extract → EDA → targets → synth (Phase 1 reorder) — extract+EDA done; targets+synth pending
- [x] **SPEC FIX (main):** CTGOV2 enum format ratified in `specs/data.md` (commit `1b65d08`); `ctgov_enums.py` normalization kept; `ECT` added as a `primary_purpose`. Code reconciliation held for review.
- [x] **Withdrawal-reason vocab**: SET expanded (commit `9e15c2c`) — added `STUDY_TERMINATED` (12.2%) + `ADMINISTRATIVE` (8.2%); censoring/ongoing now excluded from the mix (183,815 vol / 2,490 rows recorded separately, never counted as dropout). `OTHER` 50.7% → **17.54%** (honest floor; arm-level dropout unchanged, trial-mean 0.2018). `vocab.py`/`clean.py`/`report.py` reconciliation HELD for review.
- [x] Sample fixture: gitignored (`ingestion/fixtures/aact_sample/`); conftest builds a temp sample (`tmp_path_factory`) so tests/CI don't need the file

## Decisions log (settled)
- Sponsor = hard tenant boundary (RLS). CRO scoped per assignment.
- ClinicalTrials.gov/AACT = build-time only; synthetic cohort proves method validity, not clinical prediction.
- pgvector (not Pinecone); LangGraph orchestration; Langfuse tracing.
- Two surfaces; public Guide isolated in three layers.
- uv toolchain; release agent prepares commits; CI runs the same checks.
