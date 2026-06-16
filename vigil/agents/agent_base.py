"""Shared agent spine (specs/rag.md § Agents) — ONE grounding/citation pipeline for all agents.

Retention / Report / Operations are the SAME spine (5.4 tools + champion-only facts + RLS vector
+ doc-grounding + grounded refusal); they differ only by their system prompt (role + what they
answer). This module is that single pipeline so the three agents cannot drift apart.

Every agent runs under a 5.4 ``ToolContext`` (full-Scope re-resolution + scoped_session, dual-axis
isolated — never sponsor_bootstrap_session). Risk facts come ONLY through the champion-allowlist
(`participant_risk_facts`); any model-performance number is grounded in the card corpus via
`search_documents`, NEVER inlined. Zero relevant retrieval → grounded refusal, never a
hallucination.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

from vigil.agents.guardrails import fence_untrusted
from vigil.agents.llm import LLMClient, LLMMessage
from vigil.agents.tools import ToolContext, participant_risk_facts, search_documents

_REFUSAL = "I don't have grounded information for that within your scope."


@dataclass(frozen=True, slots=True)
class AgentAnswer:
    content: str
    citations: list[dict] = field(
        default_factory=list
    )  # {source_type, source_id, locator}
    status: Literal["ok", "refused"] = "ok"


def grounded_answer(
    ctx: ToolContext,
    llm: LLMClient,
    question: str,
    *,
    system_prompt: str,
    participant_id: str | None = None,
    k: int = 4,
) -> AgentAnswer:
    """Grounded, cited answer (or grounded refusal) — the shared Retention/Report/Operations body.

    Identical retrieval mechanics for every agent: champion-only participant facts (when a
    participant is named) + RLS-scoped vector retrieval; both reached only through the 5.4 tools,
    so the dual-axis (cross-tenant + cross-site) isolation guarantee holds for ALL agents. The only
    per-agent variable is ``system_prompt``.
    """
    citations: list[dict] = []
    context_blocks: list[str] = []

    # 1. Structured risk facts (champion-only, dual-axis scoped) when a participant is named.
    if participant_id:
        facts = participant_risk_facts(ctx, participant_id)
        if facts is not None:
            citations.append(
                {
                    "source_type": "structured",
                    "source_id": f"participant:{facts.participant_id}",
                    "locator": f"model_version={facts.model_version}",
                }
            )
            context_blocks.append(
                fence_untrusted(
                    f"participant_id={facts.participant_id} risk_score={facts.risk_score} "
                    f"risk_band={facts.risk_band} top_factors={facts.top_factors} "
                    f"model_version={facts.model_version} synthetic={facts.synthetic}",
                    label="participant_risk",
                )
            )

    # 2. Vector retrieval over the doc/card corpus (RLS-scoped) — grounds method/metric claims.
    for hit in search_documents(ctx, question, k=k):
        citations.append(
            {
                "source_type": "document",
                "source_id": hit.source_ref,
                "locator": f"chunk#{hit.chunk_index}",
            }
        )
        context_blocks.append(fence_untrusted(hit.chunk_text, label=hit.source_ref))

    # 3. Grounded refusal: nothing retrieved → refuse, never hallucinate.
    if not context_blocks:
        return AgentAnswer(content=_REFUSAL, citations=[], status="refused")

    grounded = "\n\n".join(context_blocks)
    resp = llm.complete(
        [
            LLMMessage("system", system_prompt),
            LLMMessage(
                "user",
                f"Grounded context:\n{grounded}\n\nQuestion: {question}\n\n"
                "Answer using only the context above and cite the sources.",
            ),
        ],
        temperature=0.0,
    )
    return AgentAnswer(content=resp.content, citations=citations, status="ok")
