#!/usr/bin/env python3
"""Vigil spec-conformance check (Phase 0).

Verifies every required spec exists and contains its required sections. This runs clean on an
empty repo (before any application code) as long as the specs are present and well-formed.
As code lands, extend this with live-invariant checks (leakage test, isolation test).

Exit 0 = conformant; exit 1 = a required spec or section is missing.
"""

from __future__ import annotations
import sys
from pathlib import Path

SPECS = Path(__file__).resolve().parent.parent / "specs"

# spec file -> required section headings
REQUIRED: dict[str, list[str]] = {
    "isolation.md": [
        "## Decisions (fixed)",
        "## MAY touch",
        "## MUST NOT touch",
        "## Proof obligation",
    ],
    "data.md": [
        "## Decisions (fixed)",
        "## Cleaned schema",
        "## Synthetic cohort",
        "## Features",
    ],
    "domain.md": ["## Decisions (fixed)", "## Roles", "## Tenancy rules"],
    "api.md": ["## Decisions (fixed)", "## JWT claim shape", "## Endpoints"],
    "rag.md": ["## Decisions (fixed)", "## Grounding rules", "## Guardrails"],
    "infra.md": ["## Decisions (fixed)", "## Topology"],
    "observability.md": [
        "## Decisions (fixed)",
        "## message_events",
        "## Admin observability",
    ],
}


def check() -> list[str]:
    problems: list[str] = []
    for fname, sections in REQUIRED.items():
        path = SPECS / fname
        if not path.exists():
            problems.append(f"MISSING SPEC: specs/{fname}")
            continue
        text = path.read_text(encoding="utf-8")
        for heading in sections:
            # match on the heading prefix so trailing words are allowed
            if not any(line.strip().startswith(heading) for line in text.splitlines()):
                problems.append(f"specs/{fname}: missing section '{heading}'")
    return problems


def main() -> int:
    problems = check()
    if problems:
        print("SPEC CONFORMANCE: FAIL")
        for p in problems:
            print(f"  - {p}")
        return 1
    print(
        f"SPEC CONFORMANCE: PASS ({len(REQUIRED)} specs, all required sections present)"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
