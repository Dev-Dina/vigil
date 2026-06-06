## Response style (terse mode) — STRICT
After any action, output ONLY: a one-line status, and a bullet list of changed files. 
Nothing else. FORBIDDEN: "What I did", "walked", "The key insight", "This ties into", 
recaps, restating the spec, explaining reasoning unless asked. Do not describe a change 
you already made. If you want to add detail, stop — the user will ask. Max ~3 lines unless 
the user asks "why" or "explain".

# Vigil — Project Memory (CLAUDE.md)

Vigil is a clinical-trial **retention intelligence** platform: it predicts participant
dropout, ranks the cohort for triage, and explains every flag. This file is loaded every
session — it is the contract for how we build. See `/specs` for detailed contracts.

## The two surfaces (never blur them)
- **Local technical app** — the full operational product (auth, tenancy, models, agents, monitoring).
- **Public Guide demo** — an isolated guest site whose chatbot explains the project from
  approved documents only. It shares **no** credentials, DB, or endpoints with the real app.

## The isolation invariant (must never break)
The public Guide can reach ONLY the approved-document vector store. No path — network,
credential, or code — to the dashboard, participant DB, model endpoints, admin APIs, or
internal tools. Enforced in three layers: separate service, separate credentials,
NetworkPolicies. See `/specs/isolation.md`.

## Engineering philosophy (Zen of Python, applied)
- Explicit > implicit: scope/tenancy/config come from trusted sources (the token, Vault),
  never from client input or ambient globals. A function that needs the tenant takes it.
- Errors never pass silently: data validation fails LOUD. No silent defaults or fallbacks.
- Simple > complex; flat > nested. Modular monolith, not microservices.
- Readability counts: type hints everywhere, `ruff` lint+format, small single-purpose functions.
- One obvious way: one data-access layer, one config object, one way to resolve scope.
- Ship each phase before starting the next.

## Architecture (strict one-directional layering)
routers (`api/`) -> services (`services/`) -> repositories (`repositories/`) -> db (`db/`)
- Routers: HTTP only (parse, validate with Pydantic, call a service, shape response). No SQL.
- Services: domain logic; orchestrate repositories; enqueue jobs.
- Repositories: the ONLY place that touches the DB; every call runs in a tenant-scoped session.
- Cross-cutting: `core/config.py`, `core/security.py`, `core/logging.py`, `core/scope.py`,
  `workers/` (Arq), `agents/` (LangGraph + MCP tools).

## Tenancy & access (fixed decisions)
- **Sponsor is the hard tenant boundary** (one sponsor never sees another), enforced by
  Postgres **row-level security** keyed on `sponsor_id` — present on every tenant table.
- The **CRO is scoped, not unlimited**: staff get access only to assigned sponsors/trials.
- Seven roles (see `/specs/domain.md`).
- **JWT carries scope** (identity, role, sponsor, assigned trials/sites); scope derived from
  the token, never asserted by the client. Sessions live in Redis (revocable).
- Secrets come from **Vault** (JWT signing key, DB creds, LLM keys). Never hardcode secrets.

## Routing — three distinct meanings
- API routing: `APIRouter` per domain under `/api/v1`; auth/scope injected as a dependency.
- Job routing: slow/expensive work (LLM/agent, reports, ingestion, drift) is NEVER inline —
  enqueue an **Arq** job, return immediately. Workers: bounded concurrency + backpressure.
- Model routing: regime routing, champion/challenger shadow, drift-triggered fallback,
  audited promotion. Every decision logged; nothing changes silently.

## Logging & observability standard
- Structured JSON logs; every line carries `request_id` (and `conversation_id` when relevant),
  role/guest scope, route. No `print`. Never log secrets or PII.
- Every chatbot/assistant message writes a `message_events` row (`/specs/observability.md`),
  with a REDACTED user message and response. Langfuse provides the trace view.

## Resilience
- Jobs idempotent, retried with exponential backoff + JITTER. Scheduled jobs jittered.
- Cost caps + model routing live at the worker.

## Data honesty (non-negotiable)
- ClinicalTrials.gov/AACT is **build-time ingestion only**, never live, never agent-reached.
- The deep-learning signal uses a **clearly-labelled synthetic cohort** calibrated to real
  aggregate statistics. Proves METHOD validity, never clinical prediction. No PHI in the build.

## Per-phase ritual (every phase, in order)
1. **Spec the artifact in `/specs` FIRST** — name which test artifact applies:
   **golden set** (transforms) / **eval set** (RAG) / **held-out split** (models). See
   `/specs/data.md` "Evaluation contract".
2. **Build to the spec.**
3. **The correct test artifact exists and is green.**
4. **spec-conformance + `release` gate before commit.**
If reality contradicts the spec, **STOP and ratify the spec on `main` first** — never diverge silently.

## Definition of done
Each phase ends in something runnable + a test. The sacred test is the **cross-tenant
leakage test**: create data as sponsor A, authenticate as sponsor B, assert invisible.
Run the spec-conformance check before considering any phase complete.
