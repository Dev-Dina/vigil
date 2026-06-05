"""Raw AACT extraction. Build-time only; one deterministic query per table.

The population filter is the slice where real per-arm started/completed/dropout counts
exist: interventional studies with reported participant-flow results. We extract server-
side SQL against a pinned monthly AACT snapshot, write newline-delimited JSON per table to
``data/raw/aact/<snapshot_date>/<table>.ndjson``, and write a ``manifest.json`` recording
the snapshot date, AACT version, source URL, exact SQL + filter predicates, and per-table
row counts.

If a live AACT Postgres is unreachable in this environment, the rest of the pipeline runs
off the committed sample fixture (see ``ingestion/fixtures/aact_sample``). This module is
parameterized so a real extraction is a drop-in.

EXPERIMENTAL / UNVERIFIED: the ``--live`` extraction path (:func:`extract_to_raw`) has NOT
been run against a real AACT Postgres snapshot in this environment. The SQL is written to
the spec but is unverified end-to-end; treat any live extract as experimental until it has
been validated against a real AACT snapshot. The committed sample fixture is the only
exercised path. Using ``--live`` emits a loud warning.
"""

from __future__ import annotations

import json
import logging
from collections.abc import Iterator
from pathlib import Path
from typing import Any

from ingestion.config import AACT_SOURCE_URL, RAW_ROOT, AactConnection

logger = logging.getLogger(__name__)

# Filter predicate, stated once and recorded verbatim in the manifest. This is the only
# slice where real per-arm started/completed/dropout counts exist.
POPULATION_FILTER = (
    "studies.study_type = 'Interventional' "
    "AND calculated_values.were_results_reported = true "
    "AND EXISTS (SELECT 1 FROM result_groups rg WHERE rg.nct_id = studies.nct_id "
    "AND rg.result_type = 'Participant Flow') "
    "AND EXISTS (SELECT 1 FROM milestones m WHERE m.nct_id = studies.nct_id)"
)

# The set of nct_ids in the modelled cohort. Every per-table query joins to this.
COHORT_CTE = f"""
WITH cohort AS (
    SELECT DISTINCT studies.nct_id
    FROM studies
    JOIN calculated_values USING (nct_id)
    WHERE {POPULATION_FILTER}
)
"""

# One deterministic query per table. Column lists match specs/data.md exactly. Each is
# ORDER BY nct_id (then group ordering where relevant) for a stable, reproducible extract.
TABLE_QUERIES: dict[str, str] = {
    "studies": COHORT_CTE
    + """
    SELECT s.nct_id, s.study_type, s.overall_status, s.phase, s.enrollment,
           s.enrollment_type, s.start_date, s.primary_completion_date,
           s.completion_date, s.number_of_arms
    FROM studies s JOIN cohort c USING (nct_id)
    ORDER BY s.nct_id
    """,
    "calculated_values": COHORT_CTE
    + """
    SELECT cv.nct_id, cv.actual_duration, cv.number_of_facilities,
           cv.were_results_reported, cv.minimum_age_num, cv.maximum_age_num
    FROM calculated_values cv JOIN cohort c USING (nct_id)
    ORDER BY cv.nct_id
    """,
    "eligibilities": COHORT_CTE
    + """
    SELECT e.nct_id, e.gender, e.minimum_age, e.maximum_age, e.healthy_volunteers,
           e.sampling_method
    FROM eligibilities e JOIN cohort c USING (nct_id)
    ORDER BY e.nct_id
    """,
    "designs": COHORT_CTE
    + """
    SELECT d.nct_id, d.allocation, d.intervention_model, d.primary_purpose, d.masking
    FROM designs d JOIN cohort c USING (nct_id)
    ORDER BY d.nct_id
    """,
    "browse_conditions": COHORT_CTE
    + """
    SELECT bc.nct_id, bc.mesh_term
    FROM browse_conditions bc JOIN cohort c USING (nct_id)
    ORDER BY bc.nct_id, bc.mesh_term
    """,
    "conditions": COHORT_CTE
    + """
    SELECT cond.nct_id, cond.downcase_name
    FROM conditions cond JOIN cohort c USING (nct_id)
    ORDER BY cond.nct_id, cond.downcase_name
    """,
    "interventions": COHORT_CTE
    + """
    SELECT i.nct_id, i.intervention_type, i.name
    FROM interventions i JOIN cohort c USING (nct_id)
    ORDER BY i.nct_id, i.name
    """,
    "sponsors": COHORT_CTE
    + """
    SELECT sp.nct_id, sp.agency_class, sp.lead_or_collaborator
    FROM sponsors sp JOIN cohort c USING (nct_id)
    ORDER BY sp.nct_id, sp.lead_or_collaborator
    """,
    "countries": COHORT_CTE
    + """
    SELECT co.nct_id, co.name, co.removed
    FROM countries co JOIN cohort c USING (nct_id)
    ORDER BY co.nct_id, co.name
    """,
    "facilities": COHORT_CTE
    + """
    SELECT f.nct_id, f.country
    FROM facilities f JOIN cohort c USING (nct_id)
    ORDER BY f.nct_id
    """,
    "result_groups": COHORT_CTE
    + """
    SELECT rg.id, rg.nct_id, rg.ctgov_group_code, rg.result_type, rg.title
    FROM result_groups rg JOIN cohort c USING (nct_id)
    WHERE rg.result_type = 'Participant Flow'
    ORDER BY rg.nct_id, rg.ctgov_group_code
    """,
    "milestones": COHORT_CTE
    + """
    SELECT m.nct_id, m.result_group_id, m.ctgov_group_code, m.title, m.period, m.count
    FROM milestones m JOIN cohort c USING (nct_id)
    ORDER BY m.nct_id, m.ctgov_group_code
    """,
    "drop_withdrawals": COHORT_CTE
    + """
    SELECT dw.nct_id, dw.result_group_id, dw.ctgov_group_code, dw.period, dw.reason,
           dw.count
    FROM drop_withdrawals dw JOIN cohort c USING (nct_id)
    ORDER BY dw.nct_id, dw.ctgov_group_code
    """,
}


def _aact_version_query() -> str:
    """AACT records its data-load metadata; the load name is the version provenance."""
    return "SELECT nlm_download_date_description FROM ctgov.data_definitions LIMIT 1"


def extract_to_raw(conn: AactConnection, out_root: Path = RAW_ROOT) -> Path:
    """Run every table query against live AACT and write NDJSON + manifest.

    Returns the snapshot directory. Requires ``psycopg`` and a reachable AACT Postgres.
    """
    # TODO(phase3): validate --live against a real AACT snapshot before real-data training
    logger.warning(
        "EXPERIMENTAL: --live AACT extraction path is UNVERIFIED against a real AACT "
        "snapshot. Treat the output as experimental until validated (snapshot_date=%s).",
        conn.snapshot_date,
    )
    import psycopg  # local import: only needed for a live extraction

    snapshot_dir = out_root / conn.snapshot_date
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    row_counts: dict[str, int] = {}
    aact_version = conn.snapshot_date
    with psycopg.connect(conn.dsn()) as pg:
        try:
            with pg.cursor() as cur:
                cur.execute(_aact_version_query())
                row = cur.fetchone()
                if row and row[0]:
                    aact_version = str(row[0])
        except Exception:  # noqa: BLE001 - version is provenance, not critical
            pass

        for table, sql in TABLE_QUERIES.items():
            with pg.cursor() as cur:
                cur.execute(sql)
                cols = [d.name for d in cur.description] if cur.description else []
                count = _write_ndjson(
                    snapshot_dir / f"{table}.ndjson",
                    (dict(zip(cols, r, strict=True)) for r in cur),
                )
                row_counts[table] = count

    write_manifest(
        snapshot_dir,
        snapshot_date=conn.snapshot_date,
        aact_version=aact_version,
        row_counts=row_counts,
    )
    return snapshot_dir


def _write_ndjson(path: Path, rows: Iterator[dict[str, Any]]) -> int:
    count = 0
    with path.open("w", encoding="utf-8") as fh:
        for row in rows:
            fh.write(json.dumps(row, default=str, sort_keys=True))
            fh.write("\n")
            count += 1
    return count


def write_manifest(
    snapshot_dir: Path,
    *,
    snapshot_date: str,
    aact_version: str,
    row_counts: dict[str, int],
) -> Path:
    """Write the provenance manifest alongside the raw extract."""
    manifest = {
        "source": "ClinicalTrials.gov / AACT (CTTI)",
        "source_url": AACT_SOURCE_URL,
        "snapshot_date": snapshot_date,
        "aact_version": aact_version,
        "build_time_only": True,
        "population_filter": POPULATION_FILTER,
        "table_sql": TABLE_QUERIES,
        "row_counts": row_counts,
    }
    path = snapshot_dir / "manifest.json"
    path.write_text(json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8")
    return path


def read_ndjson(path: Path) -> list[dict[str, Any]]:
    """Read an NDJSON table into a list of dicts. Used by the clean stage."""
    if not path.exists():
        return []
    out: list[dict[str, Any]] = []
    with path.open(encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if line:
                out.append(json.loads(line))
    return out
