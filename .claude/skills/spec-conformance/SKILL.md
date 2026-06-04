---
name: spec-conformance
description: Use before considering any phase or task complete to verify the repo conforms to /specs. Checks that every required spec exists with its required sections and (as code lands) that key invariants hold. Trigger at the end of any phase, before a commit, or when asked to verify conformance.
---

# Spec conformance (Vigil)

The spec is the source of truth; this check is the gate.

## What to run
1. `uv run python scripts/check_specs.py` — verifies every required spec exists and contains its
   required sections. Must exit 0 before a phase is "done".
2. As code lands, also verify the live invariants:
   - the cross-tenant leakage test passes (sponsor A data invisible to sponsor B);
   - no secret or raw PII appears in logs or fixtures;
   - the public Guide has no import path or credential reaching a deny-list resource
     (`/specs/isolation.md`).

## On failure
Report which spec/section/invariant failed and stop. Do not "work around" a failing
conformance check — fix the code or update the spec deliberately.
