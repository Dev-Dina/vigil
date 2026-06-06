"""Build the ingestion GOLDEN SET — the raw -> ``ref_*`` clean-transform oracle.

The golden set is a frozen slice of **REAL PUBLIC AACT trial-level data** (snapshot
``2026-06-05``) committed alongside its **expected** cleaned output. The clean transform is
asserted to reproduce ``expected/`` from ``raw/`` (see ``tests/test_golden_oracle.py``).

Scope (per ``specs/data.md`` "Evaluation contract"): this is **solely** the ingestion
clean-transform oracle (raw -> ref_*). It is NOT a model held-out split and NOT a RAG eval
set. One golden set, ingestion only.

PHI: NONE. The slice carries only the trial-level / aggregate columns the extractor pulls
(study metadata, eligibility ranges, aggregate participant-flow counts, coded withdrawal
reasons). No participant-level data, no facility names/addresses, no synthetic rows.

Run (regenerates the committed artifacts from the real snapshot on disk):

    uv run python -m tests.golden.build_golden

Requires the real raw snapshot at ``data/raw/aact/<SNAPSHOT_DATE>/`` and the real cleaned
``data/clean/`` on disk (the deterministic, RNG-free selection stratifies over the cleaned
tables). The committed ``raw/`` + ``expected/`` + ``selection.json`` are what the suite uses;
this script only rebuilds them.
"""

from __future__ import annotations

import itertools
import json
from pathlib import Path

import pandas as pd

from ingestion.clean import clean_snapshot
from ingestion.config import CLEAN_ROOT, GOLDEN_RAW_ROOT, GOLDEN_ROOT, RAW_ROOT
from ingestion.report import QualityReport

# The pinned real snapshot the golden set is frozen from (its date is the provenance key).
SNAPSHOT_DATE = "2026-06-05"

# Selection axes (deterministic, no RNG). Scoped modelling phases per the Phase-3 plan; the
# two lead sponsor classes that dominate the modelled cohort.
SELECT_PHASES = ["PHASE1/PHASE2", "PHASE2", "PHASE2/PHASE3", "PHASE3"]
SELECT_SPONSOR_CLASSES = ["ACADEMIC_OTHER", "INDUSTRY"]
PER_CELL = 2

# Every raw table the clean stage reads (one NDJSON file per table in a snapshot dir).
RAW_TABLES = [
    "studies",
    "calculated_values",
    "eligibilities",
    "designs",
    "browse_conditions",
    "conditions",
    "interventions",
    "sponsors",
    "countries",
    "facilities",
    "result_groups",
    "milestones",
    "drop_withdrawals",
]

EXPECTED_TABLES = ("ref_trial", "ref_arm", "ref_withdrawal_reason")


def _raw_snapshot_dir() -> Path:
    snap = RAW_ROOT / SNAPSHOT_DATE
    if not snap.exists():
        raise FileNotFoundError(
            f"real raw snapshot not found at {snap}; run a --live extract first "
            f"(the golden set is frozen from the real {SNAPSHOT_DATE} snapshot)."
        )
    return snap


def select() -> dict:
    """Deterministically pick the golden NCTs from the REAL cleaned snapshot.

    Cell key = (phase x sponsor_class x has_withdrawal x has_max_age). Within each cell, take
    the first ``PER_CELL`` NCTs sorted ascending. Every cell in the full cross product is
    REQUIRED: a missing or under-filled cell fails loud (the oracle must span every stratum).

    Reads ``data/clean/ref_*`` (already clean-eligible by construction, so the slice re-cleans
    without a validation failure) — no RNG anywhere.
    """
    trial = pd.read_parquet(CLEAN_ROOT / "ref_trial.parquet")
    wd = pd.read_parquet(CLEAN_ROOT / "ref_withdrawal_reason.parquet")
    has_wd = set(wd["nct_id"].unique())

    t = trial[
        trial["phase"].isin(SELECT_PHASES)
        & trial["sponsor_class"].isin(SELECT_SPONSOR_CLASSES)
    ].copy()
    t["has_withdrawal"] = t["nct_id"].isin(has_wd)
    t["has_max_age"] = t["max_age_years"].notna()

    cells: dict[str, list[str]] = {}
    selected: list[str] = []
    missing: list[str] = []
    for phase, sponsor, has_wd_v, has_age_v in itertools.product(
        SELECT_PHASES, SELECT_SPONSOR_CLASSES, [False, True], [False, True]
    ):
        sub = t[
            (t["phase"] == phase)
            & (t["sponsor_class"] == sponsor)
            & (t["has_withdrawal"] == has_wd_v)
            & (t["has_max_age"] == has_age_v)
        ]
        ncts = sorted(sub["nct_id"].unique())[:PER_CELL]
        key = f"{phase}|{sponsor}|wd={has_wd_v}|max_age={has_age_v}"
        cells[key] = ncts
        if len(ncts) < PER_CELL:
            missing.append(f"{key} (only {len(ncts)})")
        selected.extend(ncts)

    if missing:
        raise ValueError(
            "golden selection: required strata under-filled (fail loud) — "
            + "; ".join(missing)
        )

    selected = sorted(selected)
    return {
        "provenance": {
            "source": "ClinicalTrials.gov / AACT (CTTI) — REAL PUBLIC trial-level data",
            "snapshot_date": SNAPSHOT_DATE,
            "phi": "NONE — trial-level/aggregate columns only, no participant data",
            "synthetic": False,
            "purpose": "ingestion clean-transform oracle (raw -> ref_*) ONLY",
        },
        "selection_rule": {
            "phases": SELECT_PHASES,
            "sponsor_classes": SELECT_SPONSOR_CLASSES,
            "cell_key": "phase x sponsor_class x has_withdrawal x has_max_age",
            "per_cell": PER_CELL,
            "order": "nct_id ascending; every cell required (fail-loud if under-filled)",
            "n_cells": len(cells),
        },
        "cells": cells,
        "nct_ids": selected,
        "n_ncts": len(selected),
    }


def _slice_raw(snapshot_dir: Path, ncts: set[str], out_raw: Path) -> dict[str, int]:
    """Filter each raw NDJSON table to the selected NCTs, preserving source order."""
    out_raw.mkdir(parents=True, exist_ok=True)
    counts: dict[str, int] = {}
    for table in RAW_TABLES:
        src = snapshot_dir / f"{table}.ndjson"
        kept = 0
        with (out_raw / f"{table}.ndjson").open("w", encoding="utf-8") as out:
            if src.exists():
                with src.open(encoding="utf-8") as fh:
                    for line in fh:
                        line = line.strip()
                        if not line:
                            continue
                        if json.loads(line).get("nct_id") in ncts:
                            out.write(line + "\n")
                            kept += 1
        counts[table] = kept
    return counts


def _write_golden_manifest(
    snapshot_dir: Path, out_raw: Path, row_counts: dict[str, int], n_ncts: int
) -> None:
    real = json.loads((snapshot_dir / "manifest.json").read_text(encoding="utf-8"))
    manifest = {
        "golden_subset": True,
        "source": real.get("source"),
        "source_kind": "GOLDEN SLICE of the REAL AACT snapshot (NOT synthetic, NO PHI)",
        "source_url": real.get("source_url"),
        "snapshot_date": real.get("snapshot_date"),
        "aact_version": real.get("aact_version"),
        "build_time_only": True,
        "population_filter": real.get("population_filter"),
        "n_ncts": n_ncts,
        "row_counts": row_counts,
        "disclaimer": (
            "REAL PUBLIC ClinicalTrials.gov/AACT trial-level data, sliced for the ingestion "
            "clean-transform oracle. NO PHI, NO synthetic rows."
        ),
    }
    (out_raw / "manifest.json").write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )


def _write_expected(frames: dict[str, pd.DataFrame], out_expected: Path) -> None:
    out_expected.mkdir(parents=True, exist_ok=True)
    for name in EXPECTED_TABLES:
        frames[name].to_csv(out_expected / f"{name}.csv", index=False)


def _write_readme() -> None:
    (GOLDEN_ROOT / "README.md").write_text(
        "# Ingestion golden set (clean-transform oracle)\n\n"
        "A frozen slice of **REAL PUBLIC ClinicalTrials.gov/AACT** trial-level data "
        f"(snapshot `{SNAPSHOT_DATE}`) committed alongside its **expected** cleaned output.\n\n"
        "- `raw/` — sliced source NDJSON (one file per AACT table) + a sliced `manifest.json`.\n"
        "- `expected/` — `ref_trial.csv` / `ref_arm.csv` / `ref_withdrawal_reason.csv`, "
        "produced by the REAL transform (`clean_snapshot`), **never hand-authored**.\n"
        "- `selection.json` — the deterministic (no-RNG) selection rule + chosen NCTs.\n"
        "- `build_golden.py` — regenerates all of the above from the real snapshot on disk.\n\n"
        "**Oracle:** `clean_snapshot(tests/golden/raw)` reproduces `expected/` "
        "(`assert_frame_equal`, via CSV — see `tests/test_golden_oracle.py`).\n\n"
        "**Scope:** this is *solely* the ingestion clean-transform oracle (raw -> ref_*). It is "
        "NOT a model held-out split and NOT a RAG eval set (per `specs/data.md` "
        '"Evaluation contract": golden = transforms, held-out split = models, eval set = RAG).\n\n'
        "**PHI:** NONE. Trial-level / aggregate columns only (study metadata, eligibility "
        "ranges, aggregate participant-flow counts, coded withdrawal reasons). No participant "
        "data, no facility names/addresses, no synthetic rows.\n",
        encoding="utf-8",
    )


def build() -> dict:
    """Regenerate ``selection.json``, ``raw/``, and ``expected/`` from the real snapshot."""
    snapshot_dir = _raw_snapshot_dir()

    sel = select()
    GOLDEN_ROOT.mkdir(parents=True, exist_ok=True)
    (GOLDEN_ROOT / "selection.json").write_text(
        json.dumps(sel, indent=2, sort_keys=True), encoding="utf-8"
    )

    ncts = set(sel["nct_ids"])
    row_counts = _slice_raw(snapshot_dir, ncts, GOLDEN_RAW_ROOT)
    _write_golden_manifest(snapshot_dir, GOLDEN_RAW_ROOT, row_counts, sel["n_ncts"])

    # Expected outputs come from the REAL transform on the slice — never hand-authored. The
    # slice is clean-eligible by construction (selected from data/clean), so fail_loud=True.
    report = QualityReport()
    frames = clean_snapshot(
        GOLDEN_RAW_ROOT,
        report=report,
        out_root=GOLDEN_ROOT / "_clean_tmp",
        live=False,
        fail_loud=True,
    )
    _write_expected(frames, GOLDEN_ROOT / "expected")
    # The parquet scratch dir is not part of the committed oracle.
    for p in (GOLDEN_ROOT / "_clean_tmp").glob("*.parquet"):
        p.unlink()
    (GOLDEN_ROOT / "_clean_tmp").rmdir()

    _write_readme()
    return {
        "n_ncts": sel["n_ncts"],
        "raw_row_counts": row_counts,
        "expected_rows": {k: len(frames[k]) for k in EXPECTED_TABLES},
    }


if __name__ == "__main__":
    result = build()
    print(f"Golden set built: {result['n_ncts']} NCTs")
    print(f"  expected rows: {result['expected_rows']}")
