---
description: Run the Vigil spec-conformance check and report results.
---
Run `uv run python scripts/check_specs.py` and report the result. If it fails, list exactly which
spec file or required section is missing, and stop — do not work around a failing check.
Then, if code exists, remind me to also verify the cross-tenant leakage test and the public
Guide isolation test per `/specs/isolation.md`.
