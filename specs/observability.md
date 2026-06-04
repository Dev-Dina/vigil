# Observability Spec

## Decisions (fixed)
- Every chatbot/assistant message (BOTH surfaces) writes one `message_events` row.
- Redaction happens BEFORE persistence; raw message is never stored. Langfuse for traces.

## message_events schema
TODO (fill types): conversation_id, request_id, role_or_guest_scope, ts, route_or_agent,
guardrail_decision, retrieved_chunks, llm_provider_model, latency_ms, token_cost_estimate,
status, redacted_user_msg, redacted_assistant_msg.

## Admin observability page
TODO: inspect incoming messages, verify guardrails fired, debug RAG retrieval, show redaction.
Behind admin RBAC.
