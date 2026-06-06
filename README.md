# Vigil — repo scaffold (Phase 0)

Clinical-trial retention platform. This scaffold contains the Phase 0 foundation:
the project memory, the spec contracts, and the Claude Code tooling.

## Tooling
Python 3.12, managed with [uv](https://docs.astral.sh/uv/). `pyproject.toml` declares the
project (`vigil`) and the dev tools (`ruff`, `pytest`). `uv sync` creates `.venv/` and installs
them; run anything via `uv run ...` (e.g. `uv run python scripts/check_specs.py`).

## Layout
- `CLAUDE.md` — project memory, loaded every Claude Code session (principles, architecture, invariants).
- `pyproject.toml` — uv-managed project metadata and dev deps (`ruff`, `pytest`).
- `specs/` — the contracts (source of truth). Start with `isolation.md` and `data.md`.
- `.claude/skills/` — `data-cleaning`, `schema-migration`, `spec-conformance`.
- `.claude/agents/` — `ingestion`, `skeleton`, `public-demo`, `release`, `eda` (read-only
  analysis of the captured AACT snapshot) subagents.
- `.claude/commands/check-specs.md` — `/check-specs` slash command.
- `scripts/check_specs.py` — the conformance check. `make check-specs`.

## Ingestion golden set (real, committed)
The clean -> synthetic -> features pipeline runs offline against the **golden set**: a frozen
slice of **REAL PUBLIC ClinicalTrials.gov/AACT** trial-level data (snapshot `2026-06-05`)
committed alongside its expected cleaned `ref_*` output. It is the ingestion clean-transform
oracle (`assert_frame_equal` of `clean_snapshot(raw)` against `expected/`) and the non-live
pipeline substrate. **NO PHI, NO synthetic rows.** It lives at `tests/golden/` and is rebuilt
from the real snapshot on disk by:

```
make golden   # uv run python -m tests.golden.build_golden
```

The committed `tests/golden/raw/` + `expected/` + `selection.json` are what the fast suite and
CI use — no network, no committed `data/`, no fabricated fixture. Per `specs/data.md`
"Evaluation contract" the golden set is **solely** the ingestion transform oracle (golden =
transforms; models use held-out splits; RAG uses eval sets).

## Phase 0 loop
1. Co-author the specs (fill the `TODO:` markers); `isolation.md` and `data.md` first.
2. Keep `make check-specs` green.
3. The subagents and skills are committed, so they are versioned and shared.

Optional: `.claude/settings.json` can pin a default model or permissions — left out here so
nothing is asserted that you have not chosen. Add it when you want it.
