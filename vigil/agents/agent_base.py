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

import re
from dataclasses import dataclass, field
from typing import Literal

from vigil.agents.guardrails import fence_untrusted
from vigil.agents.llm import LLMClient, LLMMessage
from vigil.agents.tools import (
    ToolContext,
    cohort_at_risk,
    participant_risk_facts,
    search_documents,
)

_REFUSAL = "I don't have grounded information for that within your scope."

# Deterministic (NOT LLM) intent match for the scoped triage tool (Gate OPS-ASSIST): "who needs
# intervention", "who's at risk in my cohort", "which participants need follow-up". Keyword-gated so
# the cohort tool is invoked ONLY for cohort/triage questions, never on every turn.
_COHORT_TRIAGE = re.compile(
    r"(who\b[^?]*\b(?:need|needs|at[- ]risk|high[- ]risk|flagged|follow[- ]?up|"
    r"intervention|contact|outreach|attention|triage)"
    r"|which\s+participants?\b[^?]*\b(?:at[- ]risk|high[- ]risk|need|follow|intervention|flag)"
    r"|\b(?:my|our)\s+cohort\b"
    r"|\bneeds?\s+(?:an?\s+)?(?:intervention|follow[- ]?up|outreach))",
    re.IGNORECASE,
)


def _is_cohort_triage_question(question: str) -> bool:
    """True for scoped 'who's at risk / who needs follow-up in my cohort' triage questions."""
    return bool(_COHORT_TRIAGE.search(question))


@dataclass(frozen=True, slots=True)
class AgentAnswer:
    content: str
    citations: list[dict] = field(
        default_factory=list
    )  # {source_type, source_id, locator}
    status: Literal["ok", "refused"] = "ok"
    # Provider usage threaded from the LLMResponse (Gate 6.2 cost/latency capture). A grounded
    # refusal makes NO generation call, so these stay at honest-zero — never fabricated.
    provider_model: str = ""
    latency_ms: int = 0
    token_cost_estimate: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0


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

    # 1b. Scoped cohort TRIAGE (Gate OPS-ASSIST): "who needs intervention / who's at risk in MY
    #     cohort". Deterministic intent match (not the LLM) → inject the caller's OWN scoped at-risk
    #     list as grounded DATA. cohort_at_risk reads ONLY under ctx.scope (RLS + dual-axis), coded
    #     refs only, champion-only facts — operational triage, never clinical advice.
    if participant_id is None and _is_cohort_triage_question(question):
        at_risk = cohort_at_risk(ctx)
        if at_risk:
            citations.append(
                {
                    "source_type": "structured",
                    "source_id": "cohort:at_risk",
                    "locator": f"n={len(at_risk)} band=high",
                }
            )
            cohort_lines = "\n".join(
                f"participant={r.coded_ref or r.participant_id} band={r.risk_band} "
                f"score={r.risk_score} top_factor={r.top_factor} "
                f"intervention_logged={r.intervention_logged} "
                f"needs_intervention={r.needs_intervention} synthetic={r.synthetic}"
                for r in at_risk
            )
            context_blocks.append(
                fence_untrusted(
                    "Caller's scoped at-risk cohort (high-risk; coded refs only; operational "
                    "triage, NOT clinical advice):\n" + cohort_lines,
                    label="cohort_at_risk",
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
                "Answer the question directly and concisely, grounded ONLY in the context above, "
                "and cite the sources.",
            ),
        ],
        temperature=0.0,
    )
    return AgentAnswer(
        content=resp.content,
        citations=citations,
        status="ok",
        # Real provider usage: model records WHICH provider answered (FailoverClient sets it);
        # cost_estimate is real tokens × the configured per-1k rate, or honest-zero if no rate.
        provider_model=resp.model,
        latency_ms=resp.latency_ms,
        token_cost_estimate=resp.cost_estimate,
        prompt_tokens=resp.prompt_tokens,
        completion_tokens=resp.completion_tokens,
    )
