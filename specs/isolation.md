# Isolation Spec — Public Guide allow/deny

The most important contract in the repo. The public Guide must be provably unable to reach
anything real.

## Decisions (fixed)
- The Guide is a SEPARATE service with its OWN deployment and OWN credentials.
- It may read ONLY the approved-document vector store.
- Enforced in three independent layers: separate service, separate credentials, NetworkPolicies.

## MAY touch (allow-list)
- The approved-document vector store (read-only): brief, architecture notes, model card,
  safety policy, deployment notes.
- Its own `message_events` sink (for guardrail proof).

## MUST NOT touch (deny-list)
- The participant database, the dashboard/API, model endpoints, admin APIs, internal tools,
  Vault, Redis, the real queue, or any real credential.

## Guardrails the Guide enforces
- Block: medical advice, diagnosis, clinical claims, prompt injection, secret extraction,
  anything outside portfolio/app-explanation scope.

## Proof obligation
Isolation is defended in depth, so it is *proven* in depth: a deny-list resource must be
unreachable by code, by credential, and by network — and must stay unreachable even when the
chatbot is actively prompted to reach it. The suite below is a release gate; any single failure
blocks the Guide from shipping. "Deny-list resource" = every entry under **MUST NOT touch**,
expressed concretely as a `host:port` / Service name per environment.

### 1. Static — no code path (CI, every PR)
- **Import-graph test**: scan the Guide service's modules and assert it imports NOTHING from the
  internal app — `repositories/`, `db/`, `services/`, `core/security.py`, `core/scope.py`,
  `agents/` internal tools, the Vault client, the Redis session client, the Arq queue client.
  Any such import fails the test.
- **Config/secret audit**: resolve the Guide's settings and assert the ONLY outbound
  credentials/URLs present are (a) the read-only vector-store endpoint, (b) its own
  `message_events` sink, (c) the LLM provider key. Assert the ABSENCE of any participant-DB DSN,
  broad Vault token, Redis URL, queue URL, or internal/admin API base URL.
- **Tool-surface test**: assert the Guide agent's registered toolset is exactly
  {approved-document vector search}. No DB tool, no HTTP-to-internal tool, no admin tool.

### 2. Behavioral — "even when prompted" (CI, every PR; egress stubbed and asserted)
- A versioned **red-team prompt suite** drives the live chatbot with jailbreak / prompt-injection
  attempts that try to make it read participant data, dump secrets or env, call an internal or
  admin endpoint, reach Vault/Redis/the queue, or give medical/diagnostic/clinical advice.
- For every prompt assert BOTH: (a) the response is a refusal with
  `guardrail_decision = blocked`, and (b) ZERO outbound calls to a deny-list host were attempted
  (the stubbed egress layer records every attempt; the count must be 0). The guarantee is
  structural — there is no tool and no route to comply, so even "the model decided to comply"
  reaches nothing.
- Each attempt writes a redacted `message_events` row (`/specs/observability.md`) so the refusal
  is auditable.

### 3. Network — NetworkPolicy denial (kind/minikube integration job; this is the demo)
- Deploy the Guide with the production NetworkPolicies. From inside the Guide pod, attempt a raw
  connection to each deny-list resource — Postgres `:5432`, Redis `:6379`, the app API Service,
  Vault `:8200`, model endpoints — and assert every one is DENIED (connection refused/timeout).
- **Positive control**: from the same pod, assert the approved-document vector store IS
  reachable. This proves the policy is selectively enforcing, not that the network is merely
  broken.
- The live demo shows a deny-list connection hanging/failing side-by-side with a vector query
  that succeeds.

### Gate
Layers 1–2 run in CI on every PR; layer 3 runs in the kind-based integration job (no paid
cluster, per `/specs/infra.md`). The whole suite is part of the `release` agent's checks — a
single failure blocks shipping the Guide.
