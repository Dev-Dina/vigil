# Ingestion golden set (clean-transform oracle)

A frozen slice of **REAL PUBLIC ClinicalTrials.gov/AACT** trial-level data (snapshot `2026-06-05`) committed alongside its **expected** cleaned output.

- `raw/` — sliced source NDJSON (one file per AACT table) + a sliced `manifest.json`.
- `expected/` — `ref_trial.csv` / `ref_arm.csv` / `ref_withdrawal_reason.csv`, produced by the REAL transform (`clean_snapshot`), **never hand-authored**.
- `selection.json` — the deterministic (no-RNG) selection rule + chosen NCTs.
- `build_golden.py` — regenerates all of the above from the real snapshot on disk.

**Oracle:** `clean_snapshot(tests/golden/raw)` reproduces `expected/` (`assert_frame_equal`, via CSV — see `tests/test_golden_oracle.py`).

**Scope:** this is *solely* the ingestion clean-transform oracle (raw -> ref_*). It is NOT a model held-out split and NOT a RAG eval set (per `specs/data.md` "Evaluation contract": golden = transforms, held-out split = models, eval set = RAG).

**PHI:** NONE. Trial-level / aggregate columns only (study metadata, eligibility ranges, aggregate participant-flow counts, coded withdrawal reasons). No participant data, no facility names/addresses, no synthetic rows.
