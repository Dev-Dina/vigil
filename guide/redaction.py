"""PII redaction BEFORE the LLM — the Guide's OWN copy (specs/isolation.md § owned safety code).

A faithful copy of the app's redactor (`vigil/agents/redaction.py`), carried HERE so the Guide
imports nothing from `vigil.*` (the structural-isolation decision). Redaction runs at the boundary
before any text reaches the model, and only the redacted form is ever persisted. Fail-loud: a
redaction error blocks the turn rather than passing raw text. Guardrail refusal logic is Gate 7.3;
this module is the redaction layer it builds on.
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
    (
        re.compile(r"\b\d{4}-\d{2}-\d{2}\b|\b\d{1,2}/\d{1,2}/\d{4}\b"),
        "[REDACTED_DATE]",
    ),
    (re.compile(r"(?<!\d)\d{7,}(?!\d)"), "[REDACTED_NUM]"),
)


class RedactionError(RuntimeError):
    """Redaction failed — the turn MUST be blocked, never sent/stored raw (fail-loud)."""


def redact(text: str) -> str:
    """Return ``text`` with direct identifiers replaced by ``[REDACTED_*]`` tokens.

    Raises :class:`RedactionError` on ANY failure — the caller blocks the turn rather than
    letting raw text through.
    """
    if text is None:
        raise RedactionError("redaction received None instead of text")
    try:
        out = text
        for pattern, token in _PATTERNS:
            out = pattern.sub(token, out)
        return out
    except Exception as exc:  # noqa: BLE001 — fail loud: block, never pass raw
        raise RedactionError(f"redaction failed: {exc}") from exc
