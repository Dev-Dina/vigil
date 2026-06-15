"""In-app agent layer (Phase 5) — the authenticated, scope-bound assistant.

This package is the IN-APP agents (Retention / Report / Operations + the router), distinct from
the fully-isolated public Guide (Phase 7, `/specs/isolation.md` — which must import NOTHING from
here). Gate 5.2 lands the safety boundary every turn passes through BEFORE any router/agent/
retrieval exists: the OpenRouter LLM client (`llm`), PII redaction before the LLM (`redaction`),
and the guardrail wrapper (`guardrails`). Router/agents/retrieval arrive in 5.4/5.5.
"""
