# Specs — the contracts

Spec-driven development: these documents are the source of truth. Code is generated and
checked against them. Fill the `TODO` markers collaboratively; do not let code drift ahead
of the spec. The conformance check (`scripts/check_specs.py`, or the `spec-conformance`
skill) verifies every required spec exists and contains its required sections.

Write order: **isolation.md** and **data.md** first (they gate the most), then domain, api,
rag, infra, observability.

Each spec has a `## Decisions (fixed)` section (settled — do not relitigate) and `TODO:`
markers for what still needs filling.
