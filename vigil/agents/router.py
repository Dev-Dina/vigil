"""The router — an LLM-classification dispatch step (specs/rag.md § Router).

NOT a trained model: no artifact, no training. A single LLM call that, given the user question +
the agent definitions, classifies intent and dispatches to exactly ONE agent — or REFUSES at the
router (does not dispatch) when the request is unclear, unsafe, clinical/diagnostic, out of scope,
or matches no agent. Dispatch is not a way past the guardrails: redaction + content guardrails run
BEFORE the router (pipeline in workers.tasks); a router refusal is a clean blocked outcome.

Fail-closed: if the LLM output cannot be parsed to a known agent, the router REFUSES (never
guesses an agent). The LLM call is stubbed in CI (StubLLMClient via VIGIL_LLM_STUB).
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from vigil.agents.llm import LLMClient, LLMMessage

#: The agents the router may dispatch to. Choices are grounded in these definitions, not a
#: hardcoded keyword tree.
AGENT_DEFINITIONS: dict[str, str] = {
    "retention": (
        "Participant retention / dropout-risk explanations — 'why is participant X flagged', "
        "what drives a risk score, a participant's risk trajectory, retention method questions; "
        "also WHICH participants in the caller's own cohort are at risk or need follow-up/"
        "intervention (a scoped operational triage list — status only, NOT clinical advice)."
    ),
    "report": (
        "Scoped reporting / aggregates — counts and distributions across the caller's cohort "
        "(e.g. how many high-risk participants in a trial)."
    ),
    "operations": (
        "Scoped operational/status questions — scoring job status, champion-of-record context, "
        "what a flag means operationally."
    ),
}

_REFUSE = "refuse"

_SYSTEM = (
    "You are the Vigil assistant ROUTER. You do not answer questions; you classify the user's "
    "intent and dispatch to exactly one agent, or refuse.\n\n"
    "Agents:\n"
    + "\n".join(f"- {name}: {desc}" for name, desc in AGENT_DEFINITIONS.items())
    + "\n\n"
    'Refuse (agent="refuse") if the request is unclear, unsafe, asks for clinical/diagnostic '
    "or treatment advice, is outside the caller's scope, or matches no agent. The dispatch is "
    "NOT a way past safety rules.\n\n"
    'Return ONLY a JSON object: {"agent": "retention"|"report"|"operations"|"refuse", '
    '"reason": "<short reason>"}.'
)


@dataclass(frozen=True, slots=True)
class RouteDecision:
    agent: str | None  # one of AGENT_DEFINITIONS keys, or None = refuse
    reason: str
    raw: str = ""
    # Usage of the router's OWN classification LLM call (Gate 6.2b cost attribution). Every turn
    # that reaches the router makes this call, so its cost is real and is attributed to the turn —
    # combined with the agent generation for answered turns, or shown alone for a router refusal.
    provider_model: str = ""
    latency_ms: int = 0
    token_cost_estimate: float = 0.0
    prompt_tokens: int = 0
    completion_tokens: int = 0

    @property
    def refused(self) -> bool:
        return self.agent is None


def _extract_json(text: str) -> dict:
    """Best-effort JSON extraction from the model output; raises on failure (→ refuse)."""
    start = text.find("{")
    end = text.rfind("}")
    if start == -1 or end == -1 or end < start:
        raise ValueError("no JSON object in router output")
    return json.loads(text[start : end + 1])


def classify(llm: LLMClient, question: str) -> RouteDecision:
    """Classify a (already redacted + guardrail-checked) question to one agent, or refuse."""
    resp = llm.complete(
        [LLMMessage("system", _SYSTEM), LLMMessage("user", question)],
        temperature=0.0,
    )

    def _decide(agent: str | None, reason: str) -> RouteDecision:
        # Carry the router-classification call's REAL usage onto every outcome (refuse included).
        return RouteDecision(
            agent,
            reason,
            resp.content,
            provider_model=resp.model,
            latency_ms=resp.latency_ms,
            token_cost_estimate=resp.cost_estimate,
            prompt_tokens=resp.prompt_tokens,
            completion_tokens=resp.completion_tokens,
        )

    try:
        data = _extract_json(resp.content)
        agent = data.get("agent")
        reason = str(data.get("reason", ""))
    except (ValueError, json.JSONDecodeError):
        return _decide(None, "router could not classify the request; refusing")
    if agent == _REFUSE or agent not in AGENT_DEFINITIONS:
        return _decide(None, reason or "no matching agent; refusing")
    return _decide(agent, reason)
