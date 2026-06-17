"""The Guide's OWN content guardrails (specs/isolation.md § Guardrails; owned-copy decision).

A Guide-owned copy of the app's guardrail logic (`vigil/agents/guardrails.py` is the reference;
it is NOT imported). Ordered at the boundary:
1. **Redact BEFORE anything** (`guide.redaction.redact`, fail-loud) — raw text never reaches the
   model or persistence; a redaction failure BLOCKS the turn.
2. **Refuse** the explicitly-disallowed categories up front (medical/clinical, prompt-injection,
   secret-extraction) — before retrieval or the LLM, so a disallowed prompt reaches neither.

Out-of-scope (off-topic) is handled by the relevance-threshold refusal in `guide.rag` (Gate 7.2),
not here. The strongest guarantee is structural (specs/isolation.md): the Guide's only tool is
approved-doc vector search — even a jailbreak has no route to anything real.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal

from guide.redaction import RedactionError, redact

GuardrailDecision = Literal["allowed", "blocked"]


@dataclass(frozen=True, slots=True)
class GuardrailResult:
    decision: GuardrailDecision
    category: str | None = None  # "clinical" | "injection" | "secret" | "redaction"
    reason: str | None = None


@dataclass(frozen=True, slots=True)
class InboundGuard:
    redacted_text: str
    result: GuardrailResult


# Conservative content-refusal patterns (own copy). Structural isolation is the strong defense;
# these block the obvious disallowed asks up front so they never reach retrieval or the LLM.
_CLINICAL = re.compile(
    r"\b(diagnos\w*|prescrib\w*|treatment for|what (?:medication|drug|dose)|"
    r"should (?:i|they|we) (?:take|stop|change)|is it (?:cancer|safe to)|"
    r"medical advice|cure|therapy recommendation|symptoms?)\b",
    re.IGNORECASE,
)
_INJECTION = re.compile(
    # "ignore … instructions" / "disregard … (previous|above|instructions)" with any qualifier in
    # between (your / all the / previous), so "ignore your previous instructions" is caught too.
    r"(\bignore\b[^.?!]{0,40}\binstructions\b|"
    r"\bdisregard\b[^.?!]{0,40}\b(?:previous|above|instructions)\b|"
    r"\bsystem prompt\b|\breveal your (?:prompt|instructions|system)\b|"
    r"\brepeat your (?:system )?prompt\b|\byou are now\b|\bact as\b|"
    r"\bexfiltrat\w*|\bjailbreak\b|\bdo anything now\b)",
    re.IGNORECASE,
)
_SECRET = re.compile(
    r"\b(api[_\s-]?key|secret key|password|vault token|env(?:ironment)? var\w*|"
    r"connection string|database (?:dsn|password|credential)|service account|"
    r"what'?s your (?:api )?key)\b",
    re.IGNORECASE,
)


def check_content(text: str) -> GuardrailResult:
    """Content-based refusal check on (already-redacted) text. Returns allowed/blocked."""
    if _INJECTION.search(text):
        return GuardrailResult("blocked", "injection", "prompt-injection attempt")
    if _SECRET.search(text):
        return GuardrailResult(
            "blocked", "secret", "secret/credential extraction attempt"
        )
    if _CLINICAL.search(text):
        return GuardrailResult(
            "blocked", "clinical", "medical/diagnostic/clinical content is out of scope"
        )
    return GuardrailResult("allowed")


def guard_inbound(raw_text: str) -> InboundGuard:
    """Redact FIRST (fail-loud), then content-check. Never returns/persists raw text."""
    try:
        redacted = redact(raw_text)
    except RedactionError as exc:
        return InboundGuard(
            redacted_text="[REDACTION_FAILED]",
            result=GuardrailResult("blocked", "redaction", str(exc)),
        )
    return InboundGuard(redacted_text=redacted, result=check_content(redacted))
