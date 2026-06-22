# Vigil — Build Methodology (navigation map)

A pointer-style guide to *how Vigil is built* so a future maintainer can navigate the process
assets without re-deriving them. This file **links** the real sources; it does not duplicate them.
If this doc and a linked source disagree, the source wins.

---

## 1. The per-phase ritual

The contract lives in [CLAUDE.md](../../CLAUDE.md) (§ "Per-phase ritual"). In order, every phase:

1. **Spec the artifact in [specs/](../../specs/) FIRST** — name which test artifact applies:
   **golden set** (transforms) / **eval set** (RAG) / **held-out split** (models), per
   [specs/data.md](../../specs/data.md) "Evaluation contract".
2. **Build to the spec.**
3. **The correct test artifact exists and is green.**
4. **spec-conformance + `release` gate before commit.**

If reality contradicts the spec, **STOP and ratify the spec on `main` first** — never diverge
silently. The "sacred" test is the cross-tenant (and cross-site, SEC-1) leakage test.

Where the ritual is encoded as tooling:
- [.claude/skills/spec-conformance/SKILL.md](../../.claude/skills/spec-conformance/SKILL.md) +
  [scripts/check_specs.py](../../scripts/check_specs.py) — the conformance gate (see §4).
- [.claude/agents/release.md](../../.claude/agents/release.md) — the release/commit prep agent
  (ruff + check_specs + leakage). **(Known-stale — see §5.)**
- [.claude/skills/progress/SKILL.md](../../.claude/skills/progress/SKILL.md) +
  [ROADMAP.md](../../ROADMAP.md) — progress tracking (source of truth for *where we are*).
- Forward-looking build-state ledger: [FUTURE_WORK.md](../../FUTURE_WORK.md).

---

## 2. Gate-family legend

Work is tracked in [ROADMAP.md](../../ROADMAP.md) as **gates**. The IDs fall into families. Meanings
are **derived from how the IDs are used in ROADMAP**; where a meaning is inferred rather than stated,
it is flagged.

### Phase-numbered gates — `<phase>.<step>`
The per-phase build sequence inside a ROADMAP phase (e.g. `5.0`–`5.7`, `6.0`–`6.4`, `7.0`–`7.5`,
`8.L3-a/b/c`, `9.0`–`9.7`). Convention: a phase opens with a `.0` **spec-reconcile** gate (spec only,
no code), then numbered build steps, ending at the phase's done-when.

### Letter families (Phase-4-and-after build tracks)
| Family | Meaning (derived) | Examples |
|---|---|---|
| **B** | **B**ackend build gates for the Phase-4 scoring + model-routing spine. | B1 (routing_state + champion resolver), B2a–B2c (engagement / LSTM champion / GBT shadow), B3 (fallback + audited promotion), B4 (provenance). Test files `tests/spine/test_b2*`. |
| **H** | Risk-score **H**istory gates (the data foundation for the trajectory sparkline). | H1 (append-history writeback), H2 / H2b (promotion-aware risk-history endpoint). |
| **L** | **L**ive-keys / live-integration gates (real external calls behind an opt-in). | L1 (reproducible live Anthropic LLM path keyed from the persistent Vault). |
| **M** | **M**LOps-loop gates (drift → alert → governed promotion). | M1 (drift detection, PSI/KS) built. M3 (registry + governed-promote) **mechanism** built + service/HTTP-tested, but **not demo-verified end-to-end** (it promotes a placeholder challenger with no trained `.pt`; drift→promote auto-delivery unwired). M2 (breach alert) + the drift→routing auto-delivery link next — see [FUTURE_WORK.md](../../FUTURE_WORK.md). |
| **D** | **D**ocker / **D**eploy containerization gates. | D1 (API + worker containers), D2 (frontend), D3 (Guide). *(Inferred from D1's body + "Frontend (D2) + Guide (D3)" forward-refs. Not to be confused with documentation gate IDs like `DOCS-DE`, or with a gate prompt's internal "PART D" labels.)* |

### Cross-cutting prefixes (one-off, not phase-bound)
| Prefix | Meaning (derived) | Examples |
|---|---|---|
| **HARD-** | **Hard**ening: add rigor/quantification to the empirical record **without upgrading any claim**. | HARD-1 (per-indication bootstrap CIs + base-rate-adjusted skill). |
| **FIX-** | Bug**fix** / drift-reconciliation: repair a found defect or doc↔code drift. | FIX-1 (doc-accuracy), FIX-2 (README rewrite), FIX-3 (CORS), FIX-4 (`/cohort/summary`), FIX-5 (`enrolled_at`). |
| **FINAL-** | **Final** cleanup/polish: repo hygiene + operator docs, no behavior change. | FINAL-1 (path scrub + RUNBOOK). |
| **CLEAN-** | **Clean**up/hygiene + small wiring. | CLEAN-1 (remove committed key-probe + wire the assistant panel). |
| **SEC-** | **Sec**urity follow-up gates (cross-phase). | SEC-1 (site-narrowing on cohort/participant reads). |

> Note: occasional one-off gate tags also appear in **code comments** (e.g. `Gate RBAC-OPS` in
> `vigil/api/routers/monitoring.py`) without a matching ROADMAP family entry. Treat those as local
> annotations, not a tracked family.

---

## 3. `.claude/` asset index

One line each on what it is for. Sources: [.claude/agents/](../../.claude/agents/),
[.claude/skills/](../../.claude/skills/), [.claude/commands/](../../.claude/commands/).

### Agents — [.claude/agents/](../../.claude/agents/)
| Agent | Phase | Purpose |
|---|---|---|
| [ingestion.md](../../.claude/agents/ingestion.md) | 1 | Fetch/clean AACT + generate the synthetic cohort (the build-time data pipeline). **(Known-stale — §5.)** |
| [eda.md](../../.claude/agents/eda.md) | 1 | Read-only Q&A over the captured real AACT reference cohort (rates, reason mix, missingness). |
| [skeleton.md](../../.claude/agents/skeleton.md) | 2 | Backend spine — Vault, Postgres+RLS, auth/JWT, Redis sessions, scoped data layer, Arq queue. |
| [public-demo.md](../../.claude/agents/public-demo.md) | 7 | The isolated public Guide site + its document-only chatbot (never wires to a real resource). |
| [release.md](../../.claude/agents/release.md) | all | Run checks (ruff + check_specs + leakage) and prepare a Conventional-Commits commit. **(Known-stale — §5.)** |

### Skills — [.claude/skills/](../../.claude/skills/)
| Skill | Purpose |
|---|---|
| [data-cleaning](../../.claude/skills/data-cleaning/SKILL.md) | Encodes the data-spec validation rules, cleaned schema, fail-loud principle for any ingestion/transform/synthetic work. |
| [schema-migration](../../.claude/skills/schema-migration/SKILL.md) | Non-negotiable tenancy rules (`sponsor_id` on every tenant table; RLS from the first migration) for any schema/model/migration change. |
| [spec-conformance](../../.claude/skills/spec-conformance/SKILL.md) | Verify the repo conforms to `/specs` before any phase/commit (drives `check_specs.py`). |
| [progress](../../.claude/skills/progress/SKILL.md) | Update/report build progress against `ROADMAP.md` + the open-TODO register. |

### Commands — [.claude/commands/](../../.claude/commands/)
| Command | Purpose |
|---|---|
| [check-specs.md](../../.claude/commands/check-specs.md) | Run `scripts/check_specs.py`, report the result, and (if code exists) remind to run the leakage + Guide-isolation tests. |

---

## 4. Spec → `check_specs` section map

[scripts/check_specs.py](../../scripts/check_specs.py) is the authority. It verifies **10** specs each
contain their required `##` section headings, plus two eval-set artifacts. (`specs/README.md` is the
index and is **not** conformance-checked.)

| Spec | Required sections (count) |
|---|---|
| [isolation.md](../../specs/isolation.md) | 6 — Decisions, MAY touch, MUST NOT touch, Proof obligation, Phase 7 ratified decisions, Phase 9 egress/routing |
| [data.md](../../specs/data.md) | 4 — Decisions, Cleaned schema, Synthetic cohort, Features |
| [domain.md](../../specs/domain.md) | 4 — Decisions, Roles, Tenancy rules, Notification routing (Phase 9) |
| [api.md](../../specs/api.md) | 3 — Decisions, JWT claim shape, Endpoints |
| [rag.md](../../specs/rag.md) | 9 — Decisions, Agents, Router, Grounding rules, Retrieval stack, Scope propagation, Guardrails, Evaluation set, Done-when |
| [infra.md](../../specs/infra.md) | 3 — Decisions, Topology, Phase 9 notification egress |
| [observability.md](../../specs/observability.md) | 5 — Decisions, message_events, Admin observability, Phase 6 contracts, Drift detection (Gate M1) |
| [scoring.md](../../specs/scoring.md) | 9 — Decisions, Scoring contract, Writeback, Tenancy, Execution model, Engagement input, Demo-scope boundary, Leakage-test invariants, Phase 9 loop |
| [dashboard.md](../../specs/dashboard.md) | 6 — Decisions, Views, Role-scoped rendering, Demo loop, Synthetic data disclosure, At-risk surface (Phase 9) |
| [routing.md](../../specs/routing.md) | 7 — Decisions, Regime routing, Champion/challenger/shadow, Drift-triggered fallback, Audited promotion, Tenancy, Leakage/isolation invariants |

**Evaluation-contract artifacts** (also enforced by `check_specs.py`):
- `tests/eval/local_assistant_eval.json` — required once `rag.md` exists; must cover dims
  `faithfulness, citation, answerable_vs_unanswerable, scope_faithfulness, metric_grounding`.
- `tests/eval/guide_eval.json` — required once `isolation.md` exists; must cover dims
  `grounded_citation, answerable_vs_unanswerable, low_relevance_refusal, out_of_scope_refusal`.

---

## 5. Known-stale meta-assets (refresh later)

Flagged by the transition review; **not** rewritten in this gate (no behavior/prompt change here).
Refresh when next touching the relevant area:

- **[.claude/agents/release.md](../../.claude/agents/release.md)** — its CI recipe (ruff +
  `check_specs` + `pytest -k leakage`) is a **subset of the real CI**. The actual
  [.github/workflows/ci.yml](../../.github/workflows/ci.yml) also runs the fast suite, the
  slow-inclusive Postgres-backed spine, and the frontend typecheck. Refresh the agent's recipe to
  match.
- **[.claude/agents/ingestion.md](../../.claude/agents/ingestion.md)** — **predates the CTGOV2 /
  NDJSON ingestion**. It describes saving "raw JSON to `data/raw/`", but the pipeline since adopted
  CTGOV2 enum codes and a pinned NDJSON snapshot (`data/raw/aact/2026-06-05/`). Refresh to reflect
  the current extract format + the ratified CTGOV2 vocab.

---

## 6. Archive

Superseded planning docs live in [docs/process/archive/](archive/):
- [2026-06-08-survival-model.md](archive/2026-06-08-survival-model.md) — the original survival-model
  plan; the deliverable shipped (Phase-3 step c — see [data/models/PHASE3_CARD.md](../../data/models/PHASE3_CARD.md)
  and [data/models/t2d/model_card_survival.md](../../data/models/t2d/model_card_survival.md)), so the
  plan is historical.
