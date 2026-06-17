#!/usr/bin/env python3
"""Vigil spec-conformance check (Phase 0).

Verifies every required spec exists and contains its required sections. This runs clean on an
empty repo (before any application code) as long as the specs are present and well-formed.
As code lands, extend this with live-invariant checks (leakage test, isolation test).

Exit 0 = conformant; exit 1 = a required spec or section is missing.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
SPECS = _ROOT / "specs"

# Evaluation contract (specs/data.md): a RAG phase REQUIRES an eval set (eval set = RAG). Once
# rag.md is present, the named local-assistant eval-set artifact must exist + carry labelled cases
# covering every required dimension. (Gate 5.7 enforcement.)
_RAG_EVAL_SET = _ROOT / "tests" / "eval" / "local_assistant_eval.json"
_RAG_EVAL_DIMENSIONS = {
    "faithfulness",
    "citation",
    "answerable_vs_unanswerable",
    "scope_faithfulness",
    "metric_grounding",
}

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
    "rag.md": [
        "## Decisions (fixed)",
        "## Agents",
        "## Router",
        "## Grounding rules",
        "## Retrieval stack",
        "## Scope propagation",
        "## Guardrails",
        "## Evaluation set",
        "## Done-when",
    ],
    "infra.md": ["## Decisions (fixed)", "## Topology"],
    "observability.md": [
        "## Decisions (fixed)",
        "## message_events",
        "## Admin observability",
        "## Phase 6 contracts",
    ],
    "scoring.md": [
        "## Decisions (fixed)",
        "## Scoring contract",
        "## Writeback",
        "## Tenancy",
        "## Execution model",
        "## Engagement (visit trajectory) input",
        "## Demo-scope boundary",
        "## Leakage-test invariants",
    ],
    "dashboard.md": [
        "## Decisions (fixed)",
        "## Views",
        "## Role-scoped rendering",
        "## Demo loop",
        "## Synthetic data disclosure",
    ],
    "routing.md": [
        "## Decisions (fixed)",
        "## Regime routing",
        "## Champion / challenger / shadow",
        "## Drift-triggered fallback",
        "## Audited promotion",
        "## Tenancy",
        "## Leakage / isolation invariants",
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

    problems.extend(_check_rag_eval_set())
    return problems


def _check_rag_eval_set() -> list[str]:
    """Evaluation contract: a RAG phase (rag.md present) REQUIRES a labelled eval set artifact."""
    if not (SPECS / "rag.md").exists():
        return []  # no RAG phase yet → nothing to require
    if not _RAG_EVAL_SET.exists():
        return [
            "RAG eval set missing: tests/eval/local_assistant_eval.json "
            "(specs/data.md Evaluation contract: eval set = RAG; specs/rag.md § Evaluation set)"
        ]
    try:
        spec = json.loads(_RAG_EVAL_SET.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        return [f"RAG eval set unreadable: {exc}"]
    cases = spec.get("cases") or []
    if not cases:
        return ["RAG eval set has no cases (tests/eval/local_assistant_eval.json)"]
    dims = {c.get("dimension") for c in cases}
    missing = _RAG_EVAL_DIMENSIONS - dims
    if missing:
        return [f"RAG eval set missing required dimensions: {sorted(missing)}"]
    return []


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
