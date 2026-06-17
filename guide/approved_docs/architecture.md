# Vigil — Architecture Notes

Vigil is a **modular monolith** (one well-layered application) plus background workers — not a
swarm of microservices. The design favors explicitness, strict layering, and provable tenant
isolation over premature distribution.

## Layered backend (one direction only)
Requests flow in exactly one direction through the layers:

```
routers (HTTP)  →  services (domain logic)  →  repositories (data access)  →  database
```

- **Routers** handle HTTP only: parse and validate the request, call a service, shape the
  response. They contain no business logic and no SQL.
- **Services** hold the domain logic and orchestrate repositories and background jobs.
- **Repositories** are the *only* place that touches the database, and every call runs inside a
  tenant-scoped session.
- A small set of cross-cutting modules handles configuration, security, logging, and scope.

## Tenancy and access control
- A **sponsor** is the hard tenant boundary — one sponsor can never see another's data. This is
  enforced in the database itself with **row-level security (RLS)** keyed on the sponsor, present
  on every tenant table, so isolation does not depend on application code remembering to filter.
- A **CRO** is *scoped*, not unlimited: its staff see only the sponsors and trials assigned to
  them.
- Access is described by **seven roles**. A signed token carries the caller's identity, role, and
  scope; the scope is always derived from the verified token, never asserted by the client.
- Secrets (signing keys, database credentials, model-provider keys) come from a secrets manager
  (Vault), never hard-coded.

## Predictions and ranking
A scoring pipeline produces per-participant risk scores and writes them back behind the same
tenant isolation. Slow or expensive work — model scoring, report generation, data ingestion — is
never done inline on the request; it is queued to background workers with bounded concurrency,
retries with jittered backoff, and cost controls.

## Model routing
Models are managed with a **champion / challenger / shadow** scheme: one champion serves
predictions, while challengers and shadow models can run alongside without affecting what users
see. Promotions are **manual and audited**, and drift can trigger an automatic, logged fallback
to a previous known-good model. Nothing changes silently — every routing decision is recorded.

## The assistant and retrieval (RAG)
The in-app assistant answers grounded, cited questions. It uses **retrieval-augmented generation**:
structured facts for risk explanations and a document corpus (model cards) for any method or
metric claim. Every answer is grounded in retrieved sources and cited; if nothing relevant is
retrieved, the assistant refuses rather than guessing. A lightweight router classifies each
question and dispatches it to the right specialized agent, or refuses at the router.

## Observability
Every assistant turn writes one redacted audit record — never raw user text — capturing the
guardrail decision, which sources grounded the answer, the model used, latency, and cost. A
read-only admin/observability view lets platform and audit roles inspect turns, confirm
guardrails fired, debug retrieval, and verify redaction. Richer per-turn traces are kept in a
tracing system; the audit record remains the durable, queryable source of truth.

## The public Guide (this service) is separate by design
This Guide is a **distinct, isolated service** with its own deployment and its own credentials.
It can reach **only** a small read-only store of approved public documents. It shares no
database, no credentials, and no network path with the operational app. That boundary is not a
convention — it is enforced in depth and tested (see the Safety Policy and Deployment Notes).
