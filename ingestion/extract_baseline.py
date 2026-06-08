"""Targeted build-time re-extract of AACT Table-1 baseline data for the T2D cohort.

This AUGMENTS the existing ``data/raw/aact/2026-06-05/`` capture (it is NOT a fresh
snapshot). It pulls the ``baseline_measurements`` / ``baseline_counts`` rows (and the
``result_type='Baseline'`` result_groups) for the exact set of Type-2-diabetes NCTs the
synthetic generator needs to calibrate Layer-3 covariates (age, sex, HbA1c, BMI) to real
aggregate Table-1 statistics.

Build-time only; never reached at runtime or by an agent. Server-side filtered to the
cohort nct_ids (``WHERE nct_id = ANY(%s)``) — never a full-table scan. Fails LOUD if the
cohort count is not exactly 755 or if AACT credentials are missing/unreachable.

These outputs are RAW aggregate calibration inputs only — they hold per-arm means / SDs /
counts (Table-1 statistics), never participant-level rows. Nothing here is cleaned into
``ref_*`` or regenerated into the synthetic cohort; that is a separate, later decision.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import pandas as pd

from ingestion.config import RAW_ROOT, AactConnection

logger = logging.getLogger(__name__)

# The pinned snapshot this re-extract augments. Same hosted AACT source as 2026-06-05.
SNAPSHOT_DATE = "2026-06-05"

# The four predicates that reproduced exactly 755 T2D NCTs in prior analysis.
MODELLING_PHASES = {"PHASE1/PHASE2", "PHASE2", "PHASE2/PHASE3", "PHASE3"}
T2D_MESH_SUBSTR = "Diabetes Mellitus, Type 2"  # distinct from Type 1; matched ILIKE
DRUG_INTERVENTION_TYPES = {"DRUG", "BIOLOGICAL"}  # UPPERCASE in this capture
EXPECTED_COHORT_SIZE = 755

# Targeted baseline queries, server-side filtered to the cohort. Deterministic ORDER BY.
BASELINE_TABLE_QUERIES: dict[str, str] = {
    "baseline_measurements": """
        SELECT bm.nct_id, bm.id, bm.result_group_id, bm.ctgov_group_code,
               bm.classification, bm.category, bm.title, bm.description, bm.units,
               bm.param_type, bm.param_value, bm.param_value_num,
               bm.dispersion_type, bm.dispersion_value, bm.dispersion_value_num,
               bm.dispersion_lower_limit, bm.dispersion_upper_limit,
               bm.explanation_of_na, bm.number_analyzed, bm.number_analyzed_units,
               bm.population_description, bm.calculate_percentage
        FROM ctgov.baseline_measurements bm
        WHERE bm.nct_id = ANY(%(ncts)s)
        ORDER BY bm.nct_id, bm.result_group_id, bm.id
    """,
    "baseline_counts": """
        SELECT bc.nct_id, bc.id, bc.result_group_id, bc.ctgov_group_code,
               bc.units, bc.scope, bc.count
        FROM ctgov.baseline_counts bc
        WHERE bc.nct_id = ANY(%(ncts)s)
        ORDER BY bc.nct_id, bc.result_group_id, bc.id
    """,
    # The existing result_groups.ndjson holds only result_type='Participant Flow'. The
    # Baseline groups are the arm key for the Table-1 rows above, written separately so
    # the original participant-flow file is untouched.
    "result_groups_baseline": """
        SELECT rg.id, rg.nct_id, rg.ctgov_group_code, rg.result_type, rg.title,
               rg.description
        FROM ctgov.result_groups rg
        WHERE rg.nct_id = ANY(%(ncts)s) AND rg.result_type = 'Baseline'
        ORDER BY rg.nct_id, rg.ctgov_group_code, rg.id
    """,
}


def compute_t2d_cohort(raw_dir: Path, clean_dir: Path) -> list[str]:
    """Reproduce the exact 755 T2D NCT set. Fails loud if the count differs."""
    trial = pd.read_parquet(clean_dir / "ref_trial.parquet")
    arm = pd.read_parquet(clean_dir / "ref_arm.parquet")
    browse = pd.read_json(raw_dir / "browse_conditions.ndjson", lines=True)
    interventions = pd.read_json(raw_dir / "interventions.ndjson", lines=True)

    industry_phase = set(
        trial.loc[
            (trial["sponsor_class"] == "INDUSTRY")
            & trial["phase"].isin(MODELLING_PHASES),
            "nct_id",
        ]
    )
    t2d_mesh = set(
        browse.loc[
            browse["mesh_term"].str.contains(T2D_MESH_SUBSTR, case=False, na=False),
            "nct_id",
        ]
    )
    drug_bio = set(
        interventions.loc[
            interventions["intervention_type"].isin(DRUG_INTERVENTION_TYPES), "nct_id"
        ]
    )
    usable_arm = set(arm.loc[(arm["started"] > 0) & arm["completed"].notna(), "nct_id"])

    cohort = industry_phase & t2d_mesh & drug_bio & usable_arm
    if len(cohort) != EXPECTED_COHORT_SIZE:
        raise RuntimeError(
            f"T2D cohort size is {len(cohort)}, expected {EXPECTED_COHORT_SIZE}. "
            f"Predicate counts: industry+phase={len(industry_phase)}, "
            f"t2d_mesh={len(t2d_mesh)}, drug/bio={len(drug_bio)}, "
            f"usable_arm={len(usable_arm)}. Refusing to proceed with a different "
            f"population — re-derive the predicates before extracting."
        )
    return sorted(cohort)


def _write_ndjson(path: Path, rows: list[dict[str, Any]]) -> int:
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, default=str, sort_keys=True))
            fh.write("\n")
    return len(rows)


def extract_baseline(
    conn: AactConnection, ncts: list[str], snapshot_dir: Path
) -> dict[str, int]:
    """Run the targeted baseline queries against live AACT, write NDJSON. Returns counts."""
    import psycopg  # local import: only needed for a live extraction

    logger.warning(
        "--live targeted baseline re-extract: hosted AACT, %d cohort NCTs, "
        "snapshot_date=%s. Build-time only; never a runtime call.",
        len(ncts),
        conn.snapshot_date,
    )
    row_counts: dict[str, int] = {}
    written: dict[str, list[dict[str, Any]]] = {}
    with psycopg.connect(conn.dsn(), connect_timeout=60) as pg:
        for table, sql in BASELINE_TABLE_QUERIES.items():
            with pg.cursor() as cur:
                cur.execute(sql, {"ncts": ncts})
                cols = [d.name for d in cur.description] if cur.description else []
                rows = [dict(zip(cols, r, strict=True)) for r in cur]
            written[table] = rows
            row_counts[table] = _write_ndjson(snapshot_dir / f"{table}.ndjson", rows)
    return row_counts


def update_manifest(
    snapshot_dir: Path, *, cohort_size: int, baseline_row_counts: dict[str, int]
) -> None:
    """Augment the existing manifest with the baseline tables. Keeps capture/snapshot date."""
    manifest_path = snapshot_dir / "manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))

    manifest["row_counts"].update(baseline_row_counts)
    manifest["table_sql"].update(BASELINE_TABLE_QUERIES)
    manifest["baseline_reextract"] = {
        "note": (
            f"targeted baseline re-extract for {cohort_size} T2D NCTs, captured "
            "2026-06-08, same hosted AACT source — AUGMENTS the 2026-06-05 capture "
            "(not a fresh snapshot). Original capture_date/snapshot_date unchanged."
        ),
        "augment_date": "2026-06-08",
        "cohort_size": cohort_size,
        "cohort_definition": (
            "DISTINCT nct_id over: ref_trial sponsor_class='INDUSTRY' AND phase IN "
            "{PHASE1/PHASE2,PHASE2,PHASE2/PHASE3,PHASE3}; browse_conditions.mesh_term "
            "ILIKE '%Diabetes Mellitus, Type 2%'; interventions.intervention_type IN "
            "{DRUG,BIOLOGICAL}; ref_arm has >=1 usable arm (started>0 AND completed NOT "
            "NULL). Reproduces exactly 755."
        ),
        "server_side_filter": "WHERE nct_id = ANY(%(ncts)s) — targeted, no full-table scan",
        "tables": sorted(baseline_row_counts),
        "phi_note": (
            "baseline_measurements / baseline_counts are aggregate Table-1 statistics "
            "(per-arm means, SDs, counts), NOT participant-level rows. No PHI."
        ),
        "scope_note": (
            "RAW calibration inputs only — not cleaned into ref_*, clean schema "
            "unchanged, synthetic cohort not regenerated."
        ),
    }
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )


def run() -> dict[str, int]:
    """End-to-end: reproduce cohort, connect live, extract, write, update manifest."""
    snapshot_dir = RAW_ROOT / SNAPSHOT_DATE
    raw_dir = snapshot_dir
    clean_dir = RAW_ROOT.parent.parent / "clean"

    ncts = compute_t2d_cohort(raw_dir, clean_dir)
    conn = AactConnection.from_env()
    counts = extract_baseline(conn, ncts, snapshot_dir)
    update_manifest(snapshot_dir, cohort_size=len(ncts), baseline_row_counts=counts)
    return counts


if __name__ == "__main__":
    from dotenv import load_dotenv

    load_dotenv(RAW_ROOT.parent.parent.parent / ".env")
    logging.basicConfig(level=logging.INFO)
    result = run()
    print("baseline re-extract row counts:", result)
