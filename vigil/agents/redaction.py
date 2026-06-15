"""PII redaction BEFORE the LLM (specs/rag.md § Guardrails → PII redaction).

Redaction runs at the boundary BEFORE the message reaches the model or any tool, and ONLY the
redacted form is ever persisted (`message_events.redacted_user_msg` / `redacted_assistant_msg`,
`/specs/observability.md`). Applies to BOTH inbound user text and outbound assistant text.

Fail-loud: if redaction errors, the turn is BLOCKED, not sent/stored raw — `redact()` raises
:class:`RedactionError`; the caller maps that to a blocked turn (never persists the raw text).

Scope note: the local assistant deals in **coded** participant ids only; an identifiable identity
is never sent to the LLM (identities are surfaced to site roles through the typed API, not the
model). This module redacts high-confidence direct identifiers (email, phone, SSN/MRN-style ids,
dates of birth, street addresses, long digit runs); free-text names are kept out structurally by
the coded-id contract, not by NER.
"""

from __future__ import annotations

import re

# Ordered (pattern, token). Order matters: more specific patterns first.
_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"), "[REDACTED_EMAIL]"),
    (re.compile(r"\b\d{3}-\d{2}-\d{4}\b"), "[REDACTED_SSN]"),
    (
        re.compile(
            r"\b(?:MRN|mrn|medical record(?:\s*(?:no|number|#))?)\s*[:#]?\s*\d{4,}\b"
        ),
        "[REDACTED_MRN]",
    ),
    (
        re.compile(
            r"\b(?:DOB|dob|date of birth|born)\s*[:#]?\s*"
            r"\d{1,4}[/-]\d{1,2}[/-]\d{1,4}\b"
        ),
        "[REDACTED_DOB]",
    ),
    (
        re.compile(
            r"(?<!\d)(?:\+?\d{1,2}[\s.-]?)?(?:\(\d{3}\)|\d{3})[\s.-]\d{3}[\s.-]\d{4}(?!\d)"
        ),
        "[REDACTED_PHONE]",
    ),
    (
        re.compile(
            r"\b\d{1,6}\s+[A-Za-z0-9.\s]{2,40}?\s"
            r"(?:Street|St|Avenue|Ave|Road|Rd|Boulevard|Blvd|Lane|Ln|Drive|Dr|Court|Ct|Way)\b\.?",
            re.IGNORECASE,
        ),
        "[REDACTED_ADDRESS]",
    ),
    # Bare DOB-style dates (yyyy-mm-dd / mm/dd/yyyy) not already caught above.
    (
        re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{4}\b"),
        "[REDACTED_DATE]",
    ),
    # Long bare digit runs (account/record numbers) — after phone/SSN so those win.
    (re.compile(r"(?<!\d)\d{7,}(?!\d)"), "[REDACTED_NUM]"),
)


class RedactionError(RuntimeError):
    """Redaction failed — the turn MUST be blocked, never sent/stored raw (fail-loud)."""


def redact(text: str) -> str:
    """Return ``text`` with direct identifiers replaced by ``[REDACTED_*]`` tokens.

    Raises :class:`RedactionError` on ANY failure — the caller blocks the turn rather than
    letting raw text through. Idempotent-ish: re-redacting redacted text is a no-op on tokens.
    """
    if (
        text is None
    ):  # defensive — a None where text is expected is a fail-loud condition
        raise RedactionError("redaction received None instead of text")
    try:
        out = text
        for pattern, token in _PATTERNS:
            out = pattern.sub(token, out)
        return out
    except Exception as exc:  # noqa: BLE001 — fail loud: block, never pass raw
        raise RedactionError(f"redaction failed: {exc}") from exc
