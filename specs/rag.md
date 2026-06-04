# RAG Spec

## Decisions (fixed)
- Local assistant: HYBRID retrieval — structured (Postgres, via MCP) for risk explanations,
  vector (pgvector) for protocols/SOPs. Answers grounded + cited; no open generation.
- Public Guide: vector retrieval over APPROVED DOCUMENTS ONLY (see isolation.md).
- LangGraph orchestration; Langfuse tracing.

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
- **Local-assistant set** additionally asserts scope-faithfulness: an explanation for a
  participant uses ONLY that caller-scoped participant's retrieved facts. **Guide set** asserts
  every answer is sourced from approved docs only — and overlaps the red-team suite
  (`/specs/isolation.md` §2): out-of-scope prompts must refuse.

## Guardrails
Layered and ordered: redaction happens **before** the LLM ever sees the text; refusal/injection
checks wrap every turn; the outcome (`allowed` / `blocked`) is written to `message_events`
(`/specs/observability.md`) for both surfaces.

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
