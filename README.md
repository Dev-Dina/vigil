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

## Data fixture (generated, never committed)
The clean -> synthetic -> features pipeline runs offline against a SAMPLE AACT fixture that
is **deterministically generated, never committed, and is not a real AACT extract**. It lives
at `ingestion/fixtures/aact_sample/` (git-ignored) and is built by:

```
make data-fixture   # uv run python -m ingestion.fixtures.build_sample
```

Real provenance comes from `ingestion.extract` against a live AACT snapshot (build-time only).
Tests and CI never depend on a committed fixture: `tests/conftest.py` regenerates the sample
into a pytest tmp dir at test time (same seed -> identical bytes), so the fast suite passes
even when `ingestion/fixtures/aact_sample/` does not exist on disk.

## Phase 0 loop
1. Co-author the specs (fill the `TODO:` markers); `isolation.md` and `data.md` first.
2. Keep `make check-specs` green.
3. The subagents and skills are committed, so they are versioned and shared.

Optional: `.claude/settings.json` can pin a default model or permissions — left out here so
nothing is asserted that you have not chosen. Add it when you want it.
