# RAG Spec

## Decisions (fixed)
- Local assistant: HYBRID retrieval — structured (Postgres, via MCP) for risk explanations,
  vector (pgvector) for protocols/SOPs. Answers grounded + cited; no open generation.
- Public Guide: vector retrieval over APPROVED DOCUMENTS ONLY (see isolation.md).
- LangGraph orchestration; Langfuse tracing.

## Grounding rules
TODO: retrieval sources per agent; citation requirement; faithfulness + citation-accuracy eval set.

## Guardrails
TODO: refuse clinical/diagnostic/out-of-scope; prompt-injection defense; PII redaction BEFORE the LLM.
