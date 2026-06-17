# RAG Spec

## Decisions (fixed)
- Local assistant: HYBRID retrieval — structured (Postgres, via MCP) for risk explanations,
  vector (pgvector) for protocols/SOPs. Answers grounded + cited; no open generation.
- Public Guide: vector retrieval over APPROVED DOCUMENTS ONLY (see isolation.md).
- LangGraph orchestration; Langfuse tracing.
- **Two distinct surfaces — never blurred.** The Phase-5 agents (Retention / Report / Operations
  + the router below) are the **IN-APP, authenticated, scope-bound** assistant (`/assistant`,
  `/specs/api.md`). They are NOT the public Guide — the Guide is a separate, fully-isolated
  Phase-7 service (`/specs/isolation.md`). This spec's § Agents / § Router / § Scope propagation
  govern the in-app agents only; the Guide's allow/deny contract lives in isolation.md.
- **Generation: Anthropic PRIMARY (`claude-haiku-4-5`), OpenRouter FALLBACK; embeddings are
  LOCAL.** Vector retrieval uses a local embedding model (e.g. `bge-small-en-v1.5` /
  `all-MiniLM-L6-v2`) — no embedding API, hermetic. Text generation (router classification + agent
  answers) calls **Anthropic** first; on a **transient** error (HTTP 429 / 5xx / timeout /
  transport) it **fails over to OpenRouter**. On a NON-transient error (401 auth / 400 bad
  request) it does NOT fail over — a primary misconfig surfaces loudly rather than being masked.
  The agent layer is the project's FIRST outbound LLM egress: egress is **allow-listed to
  `api.anthropic.com` + `openrouter.ai` ONLY**, consistent with the deny-by-default posture
  (`/specs/isolation.md`). Provider keys come from Vault, never inlined.
- **CI is hermetic — fake/recorded LLM responses for BOTH providers.** Router classification and
  agent generation use the stubbed client in CI (`VIGIL_LLM_STUB=true` → `StubLLMClient`, selected
  BEFORE any real client is constructed): deterministic, no network, no key for Anthropic OR
  OpenRouter. Real providers are reached only on local/demo runs. The eval set (§ Evaluation set)
  scores against recorded fixtures so it is reproducible.

## Agents
Three dedicated in-app agents, each **scope-bound to the caller** (it can only ever see what the
caller's token grants — § Scope propagation) and each subject to the same grounding (§ Grounding
rules) and guardrails (§ Guardrails). None can be talked into reaching data outside the caller's
scope; structurally the scoped tools return nothing out-of-scope and the agent refuses rather
than speculating.

- **Retention agent — participant-risk explanations.** Answers "why is participant X flagged":
  per-feature contributions, risk band, status, trajectory. Tools: the scoped structured source
  ONLY through the committed risk services (§ Grounding rules → Champion-allowlist binding) —
  `/risk`, `/risk/history`, `/cohort`, participant-detail — plus the model-card corpus for any
  method/metric claim (§ Grounding rules → Doc-grounding). Refuses: clinical/diagnostic/care
  advice; any participant/trial outside scope (returns nothing → refusal, never a leak).
- **Report agent — scoped reporting / aggregates.** Answers cohort- and trial-level reporting
  questions ("how many high-risk participants in trial T", band distributions, intervention
  counts) computed ONLY over caller-scoped rows via the same scoped services/session. Tools:
  scoped structured source + card corpus. Refuses: out-of-scope aggregates; anything requiring a
  cross-tenant or cross-site read; clinical claims.
- **Operations agent — scoped operational queries.** Answers operational/status questions within
  scope (scoring job status, model/champion-of-record context, what a flag means) grounded in the
  committed services + the card/doc corpus. Refuses: model promotion/fallback actions (those are
  the audited platform_admin path, `/specs/routing.md` — never an agent action), out-of-scope
  reads, clinical claims.

## Router
A thin **LLM-classification dispatch step** in front of the three agents.
- **It is NOT a trained model.** No classifier artifact, no training, no held-out split — it is a
  single LLM call that, given the user question + the agent definitions above, classifies intent
  and dispatches to exactly one agent. Its choices are grounded in the agent definitions
  (this spec / the agent docs), never a hardcoded intent table.
- **The router REFUSES at the router** — it does not forward — when the request is unclear,
  unsafe, clinical/diagnostic, out-of-scope, or matches no agent. Dispatch is NOT a way past the
  guardrails: redaction (§ Guardrails) runs before the router LLM ever sees the text, and a
  router refusal is an explicit `guardrail_decision = blocked` outcome written to `message_events`.
- The router LLM call is subject to the same CI-stub posture (§ Decisions): recorded/fake
  responses in CI, real OpenRouter only on local/demo.

### Routing-approach decision (ratified)
**DECISION:** the router classifies intent via an **LLM call** (a classification prompt over the
agent definitions), **NOT a trained ML classifier.**
- **RATIONALE:** no labeled routing dataset exists, and within the project timeline building a
  trained classifier (dataset creation → training → eval → versioning) was not justified. An
  LLM-classification prompt is correct-by-construction against the agent definitions with **zero
  training data**, and fails closed to a refusal on any unparseable output.
- **COST IMPLICATION:** every turn that reaches the router makes one classification LLM call, so
  routing has a **real per-turn token cost**. This cost is **attributed** (Gate 6.2b): the
  `message_events` row's `token_cost_estimate` / `latency_ms` reflect the TURN TOTAL —
  router-classification + agent-generation for an answered turn; the router's own (real, small)
  cost alone for a refusal-at-router; genuinely **zero** only when a pre-router guardrail block
  made no LLM call. Cost stays real-or-honest-zero (real tokens × configured rate), never faked.
- **FUTURE PATH:** once enough labeled routing data accumulates (e.g. mined from `message_events`:
  redacted question → chosen agent), a small **trained intent classifier** could replace the LLM
  router — cheaper and faster per turn, with no per-turn LLM cost. This is a **data-dependent
  future optimization**, not a current gap: LLM-routing is the right choice NOW given no data +
  timeline; a trained classifier is the improvement when the data exists.

## Grounding rules
Nothing is generated free-hand: every claim an agent makes is traceable to a retrieved source,
and a turn that retrieves nothing relevant refuses rather than improvises. The two surfaces have
**different retrieval sources but the same grounding contract**.

### Retrieval sources per agent
**Local assistant (`/assistant`, authenticated, scope-bound)** — HYBRID, two source classes,
both reached only through MCP tools the agent is explicitly granted:
- **Structured** (risk explanations, cohort/participant facts): Postgres via the scoped MCP tool.
  The tool runs in the caller's **tenant-scoped session** — RLS applies, so retrieval can only see
  participants/trials inside the caller's scope. A `participant_id` outside scope returns nothing
  (never an error that leaks existence). This is the source for "why is X flagged": per-feature
  contributions, status, history.
- **Vector** (protocols, SOPs, study reference): pgvector over the sponsor-scoped document
  collection, filtered by `sponsor_id` so one sponsor never retrieves another's documents.
- Public AACT reference (`ref_trial` / `ref_arm` / `ref_withdrawal_reason`, `/specs/data.md`) is
  RLS-exempt and may be retrieved for population-level context — clearly labelled as aggregate
  reference, never presented as a participant fact.

**Public Guide (isolated service, guest)** — vector retrieval over the **approved-document vector
store ONLY** (`/specs/isolation.md`): brief, architecture notes, model card, safety policy,
deployment notes. Its registered toolset is exactly `{approved-document vector search}` — no DB
tool, no structured source, no path to anything tenant-scoped. If the approved docs don't answer,
it refuses; it never falls back to open generation or another source.

### Champion-allowlist binding (HARD RULE)
Agents read risk facts ONLY through the committed risk services — the **champion-allowlist
surfacing** (`scoring.get_surfaceable_score(champion_versions=routing.champion_model_versions())`
and the `/risk` · `/risk/history` · `/cohort` · participant-detail paths, `/specs/scoring.md` §
Champion-only surfacing). An agent MUST NOT query `participant_score` (or any score table) raw.
Consequence: a shadow / challenger / non-champion row can NEVER reach an answer — the same
guarantee the dashboard surfaces hold (B2c/H2b). A participant with no champion score yields "no
grounded answer", never a non-champion fallback.

### Doc-grounding for metrics (HARD RULE, ratified)
Any model-performance / metric claim an agent OR the router surfaces — PR-AUC, lift, calibration,
`n`, base rate, C-index — MUST be retrieved from the **model-card corpus**
(`data/models/**/*model_card*.md` + `data/models/PHASE3_CARD.md`) and cited like any other chunk.
It is NEVER a hardcoded constant, an inlined literal, or a test-asserted number. Rationale: the
deferred hardening track updates the cards (CIs, corrected `n_test_arms`, decomposition); grounding
every metric in the corpus means RAG inherits the corrected truth with **zero rework**. A
performance claim with no card citation is a faithfulness failure (§ Evaluation set, metric-grounding
case).

### Citation requirement
- Every substantive answer cites its sources. A turn with zero retrieved chunks → refusal
  ("I don't have a grounded answer for that"), never an uncited claim.
- Citation shape: `{source_type, source_id, locator}` — e.g.
  `{"structured", "participant:risk", "model_version=v3"}` for a risk fact,
  `{"document", "doc_id", "chunk_id/page"}` for a doc chunk. The Guide cites the approved doc +
  chunk; for portfolio docs it surfaces a `study_url` / doc link where one exists.
- Answers carry inline markers tied to the citation list; an assistant claim that cannot be mapped
  back to a retrieved chunk is a faithfulness failure (caught by the eval set below).
- The retrieved chunk ids are persisted on the `message_events` row (`retrieved_chunks`,
  `/specs/observability.md`) so any answer is auditable after the fact.

### Faithfulness + citation-accuracy eval set
A versioned, labelled eval set (one per surface) is a release gate, scored in CI and traced in
Langfuse:
- **Faithfulness**: every assertion in the answer is entailed by a cited chunk — no unsupported or
  hallucinated claims. Scored per-claim; threshold gates the build.
- **Citation accuracy**: each citation actually supports the sentence it's attached to (no
  mis-citation), and nothing material is left uncited. Precision + recall over citations.
- **Answerable vs unanswerable**: the set includes out-of-corpus questions whose correct behavior
  is a grounded refusal; "answered anyway" is a failure.
- **Metric-grounding**: a question asking for a model-performance number (PR-AUC, lift, `n`, …)
  is answered ONLY with a card-corpus citation (§ Grounding rules → Doc-grounding); a performance
  claim with no card citation, or one citing an inlined constant, is a failure.
- **Local-assistant set** additionally asserts scope-faithfulness: an explanation for a
  participant uses ONLY that caller-scoped participant's retrieved facts. **Guide set** asserts
  every answer is sourced from approved docs only — and overlaps the red-team suite
  (`/specs/isolation.md` §2): out-of-scope prompts must refuse.

## Retrieval stack
- **Structured source** — the scoped committed services (§ Grounding rules → Champion-allowlist
  binding), run under the caller's RLS session (§ Scope propagation). Never raw SQL against score
  tables.
- **Vector source** — **LOCAL embeddings** (e.g. `bge-small-en-v1.5` / `all-MiniLM-L6-v2`, no
  embedding API, hermetic) over **pgvector**, every query `sponsor_id`-filtered under RLS so one
  sponsor never retrieves another's chunks (same FORCE-RLS fail-closed posture as
  `participant_score`, `/specs/scoring.md` § Tenancy).
- **Phase-5 doc corpus = the grounding corpus only**: the tracked model cards
  (`data/models/**/*model_card*.md`) + `data/models/PHASE3_CARD.md`. Sponsor-scoped protocol/SOP
  collections (rag.md § Retrieval sources mentions them) are **DEFERRED — not Phase 5**; they
  arrive with a later doc-ingestion gate. The AACT public reference (`ref_*`, `/specs/data.md`) may
  be retrieved for aggregate population context, clearly labelled, never as a participant fact.
- **Generation** — OpenRouter (free model); CI uses recorded/fake responses (§ Decisions).

## Scope propagation
The critical in-app invariant: **agents run as Arq jobs, and the job reaches data STRICTLY within
the caller's scope, through the verified RLS machinery — never around it.**
- The job MUST **re-resolve the FULL caller `Scope` at job time** — `user_id` → `resolve_scope`
  (`vigil/services/scope_resolver.py`), the freshest-grants / smallest-trust-surface path (the
  same resolution login uses) — and then run **every** tool through `scoped_session(scope)`
  (`vigil/repositories/session.py`).
- It MUST NOT use `sponsor_bootstrap_session` and MUST NOT open a raw session factory.
  **Why:** `sponsor_bootstrap_session` binds sponsor-only and drops the site/trial **tuple
  narrowing** that `scoped_session(scope)` enforces — so a site-scoped role's agent would see the
  whole sponsor's participants, a **cross-SITE leak within a tenant**.
- **Sacred extension (adversarial on BOTH axes).** The Phase-5 sacred test asserts: an agent for
  Sponsor A cannot reach another sponsor's data (cross-tenant), AND an agent for a site-scoped
  role inside A cannot reach another site's data within A (cross-site-within-tenant). An
  out-of-scope id returns **empty**, never an error that leaks existence, never a non-champion or
  cross-scope row. The test pins the binding: the agent job resolves scope and uses
  `scoped_session`, never `sponsor_bootstrap_session` / a raw factory.

## Guardrails
Layered and ordered: redaction happens **before** the LLM ever sees the text; refusal/injection
checks wrap every turn; the outcome (`allowed` / `blocked`) is written to `message_events`
(`/specs/observability.md`) for both surfaces.

**Phase split (corrects the roadmap prose):** the `message_events` **WRITE path is Phase 5** — a
turn that produces no event row is un-auditable, so writing the redacted row (with
`guardrail_decision` + `retrieved_chunks`) is part of every Phase-5 agent turn. The **admin /
inspect surface + Langfuse view + monitoring/cost screen wiring is Phase 6**
(`/specs/observability.md` § Admin observability). Phase 5 builds the table + the write; Phase 6
builds the human-facing view over it.

### PII redaction BEFORE the LLM
- The raw user message is redacted at the boundary **before** it reaches the model or any tool,
  and the raw text is never persisted — only the redacted form is stored
  (`redacted_user_msg` / `redacted_assistant_msg`, `/specs/observability.md`).
- Redaction covers direct identifiers (names, emails, phones, MRNs, addresses, dates of birth) on
  both the inbound message and the outbound response. The local assistant deals in **coded**
  participant ids only; an identifiable identity is never sent to the LLM (identities are surfaced
  to site roles through the typed API, not the model — `/specs/api.md`).
- Redaction failing is fail-loud: if the redactor errors, the turn is blocked, not sent raw.

### Refuse clinical / diagnostic / out-of-scope
- Both surfaces refuse medical advice, diagnosis, treatment recommendations, and clinical claims.
  Vigil explains **retention risk and the method**; it does not advise on a participant's care.
- The Guide additionally refuses anything outside portfolio / app-explanation scope (it only
  describes the project from approved docs).
- The local assistant refuses requests outside the caller's scope — it cannot be talked into
  describing a participant or trial the token doesn't grant; structurally the scoped tool returns
  nothing, and the agent refuses rather than speculating.
- A refusal is an explicit, logged outcome (`guardrail_decision = blocked`), not a silent empty
  answer.

### Prompt-injection defense
- Retrieved document content and tool output are treated as **data, not instructions**: the system
  prompt fences untrusted content and the agent is instructed to never follow directives found
  inside retrieved text (e.g. "ignore previous instructions", "exfiltrate…").
- Defense is **structural, not just prompt-based** — the strongest guarantee is that there is no
  tool and no route to comply: the Guide can reach only the approved-doc vector store, and the
  local assistant can reach only the caller-scoped session. Even a successful jailbreak reaches
  nothing outside the allow-list.
- Attempts to extract secrets/env, reach internal/admin endpoints, or dump another tenant's data
  are blocked AND produce zero outbound calls to any deny-list host — asserted by the red-team
  suite and egress check in `/specs/isolation.md` §2.
- Every attempt writes a redacted `message_events` row so the refusal is auditable.

## Evaluation set
A versioned, labelled **local-assistant RAG eval set** is the named test artifact for this phase
(per `/specs/data.md` Evaluation contract: **eval set = RAG**) and a **release gate scored in CI**
against recorded LLM fixtures (§ Decisions, hermetic). It comprises the criteria detailed under §
Grounding rules → Faithfulness + citation-accuracy eval set:
- faithfulness (every assertion entailed by a cited chunk),
- citation precision + recall,
- answerable-vs-unanswerable (out-of-corpus → grounded refusal),
- scope-faithfulness (an answer uses ONLY the caller-scoped retrieved facts),
- **metric-grounding** (a performance number is answered only with a card-corpus citation; an
  inlined/uncited metric is a failure — § Grounding rules → Doc-grounding).
Thresholds gate the build; `scripts/check_specs.py` is extended (Gate 5.7) to require this eval
set for the RAG phase.

## Done-when
Phase 5's done-when ("in-scope answered with citations; clinical/out-of-tenant refused") is met by
the **router + the Retention agent end-to-end**: router classifies an in-scope question → dispatches
to the scope-bound Retention agent → hybrid RAG retrieval under `scoped_session` → a cited answer →
a redacted `message_events` row; a clinical / out-of-tenant / out-of-site / out-of-corpus request
is refused (`guardrail_decision = blocked`) and reaches no scoped data. The **Report** and
**Operations** agents are follow-on behind the same router / scope / guardrail spine.
