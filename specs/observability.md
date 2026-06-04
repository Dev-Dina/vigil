# Observability Spec

## Decisions (fixed)
- Every chatbot/assistant message (BOTH surfaces) writes one `message_events` row.
- Redaction happens BEFORE persistence; raw message is never stored. Langfuse for traces.

## message_events schema
One row per chatbot/assistant message on BOTH surfaces (local assistant + public Guide). Written
AFTER redaction — the raw message is never stored. The Guide writes to its OWN sink; the app
writes to the app sink; same schema, separate stores (`/specs/infra.md`).

| column | type | rule / notes |
|---|---|---|
| `id` | uuid PK | row id |
| `conversation_id` | uuid | groups a turn sequence; from `/assistant/conversations` or the Guide session |
| `request_id` | str | per-request correlation id; matches the structured log line |
| `role_or_guest_scope` | str | app: the caller role (e.g. `study_manager`); Guide: `guest` |
| `surface` | enum | `local_assistant` \| `public_guide` — which surface emitted the row |
| `ts` | timestamptz | event time (UTC) |
| `route_or_agent` | str | API route or LangGraph agent/node that handled the turn |
| `guardrail_decision` | enum | `allowed` \| `blocked` (a refusal is an explicit logged outcome) |
| `retrieved_chunks` | jsonb | list of citation refs `{source_type, source_id, locator}` (`/specs/rag.md`); `[]` if none |
| `llm_provider_model` | str | provider + model id used (e.g. `anthropic/claude-...`) |
| `latency_ms` | int | end-to-end turn latency, ≥ 0 |
| `token_cost_estimate` | numeric | estimated cost (USD); input+output tokens × rate |
| `status` | enum | `ok` \| `refused` \| `error` |
| `redacted_user_msg` | text | PII-redacted inbound message; raw text NEVER stored |
| `redacted_assistant_msg` | text | PII-redacted response; raw text NEVER stored |

Notes: redaction is fail-loud (`/specs/rag.md`) — if it errors the turn is blocked, not stored
raw. No secrets or PII in any column. `retrieved_chunks` makes every answer auditable back to its
sources. Langfuse holds the full trace; this table is the durable, queryable audit record.

## Admin observability page
A read-only operations view behind **admin RBAC** (ML/platform admin and auditor per
`/specs/domain.md`; `GET /monitoring/messages`, `/specs/api.md`). It reads `message_events` only —
never raw messages (none exist) — and is the human-facing proof that guardrails and redaction work.
- **Inspect messages**: browse/filter `message_events` by `surface`, `conversation_id`,
  `role_or_guest_scope`, `guardrail_decision`, `status`, time window. Shows the redacted user +
  assistant text, route/agent, model, latency, and cost estimate.
- **Verify guardrails fired**: surface `guardrail_decision = blocked` turns (including the Guide
  red-team attempts, `/specs/isolation.md` §2) so a reviewer can confirm refusals happened and are
  auditable.
- **Debug retrieval**: expand `retrieved_chunks` to see which sources/citations grounded an answer
  — diagnose a bad or empty retrieval, with a deep link to the Langfuse trace.
- **Show redaction**: display only the redacted fields, demonstrating raw PII is never persisted;
  the page has no path to any unredacted text.
- Auditor scope is read-only (no actions); admin may not see identifiable participant data — the
  page exposes coded ids only.
