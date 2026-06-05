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

The ``--live`` extraction path (:func:`extract_to_raw`) has been exercised against a real
hosted AACT Postgres (snapshot captured 2026-06-05; connection and per-table queries
verified to execute). This host serves AACT's CTGOV2 *uppercased* enum codes
(``study_type='INTERVENTIONAL'``, ``phase='PHASE2'``, ``allocation='RANDOMIZED'``,
``gender='ALL'`` ...), which — as ratified in ``specs/data.md`` ("Enum format (CTGOV2)") —
are the canonical values pinned by :mod:`ingestion.schema`. The population filter below
therefore uses the CTGOV2 ``'INTERVENTIONAL'`` literal and the captured snapshot flows
through ``clean`` end-to-end with no enum contradiction.
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
#
# specs/data.md (ratified "Enum format (CTGOV2)") pins study_type = 'INTERVENTIONAL', the
# CTGOV2 uppercased code the hosted AACT snapshot stores. The filter uses that canonical
# value directly and the clean stage validates against the same CTGOV2 enums.
STUDY_TYPE_LITERAL = "INTERVENTIONAL"  # CTGOV2 canonical value pinned by specs/data.md


def _population_filter(study_type_literal: str) -> str:
    return (
        f"studies.study_type = '{study_type_literal}' "
        "AND calculated_values.were_results_reported = true "
        "AND EXISTS (SELECT 1 FROM result_groups rg WHERE rg.nct_id = studies.nct_id "
        "AND rg.result_type = 'Participant Flow') "
        "AND EXISTS (SELECT 1 FROM milestones m WHERE m.nct_id = studies.nct_id)"
    )


# The population filter, recorded verbatim in the manifest and executed against AACT.
POPULATION_FILTER = _population_filter(STUDY_TYPE_LITERAL)

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


# AACT exposes its load metadata differently across deployments. Try a few known sources;
# whichever resolves first becomes the version provenance. Each runs in its own transaction
# so a missing relation never poisons the extract transaction.
_AACT_VERSION_QUERIES: tuple[str, ...] = (
    "SELECT nlm_download_date_description FROM ctgov.data_definitions LIMIT 1",
    "SELECT max(nlm_download_date) FROM ctgov.calculated_values",
    "SELECT max(updated_at) FROM ctgov.studies",
)


def extract_to_raw(conn: AactConnection, out_root: Path = RAW_ROOT) -> Path:
    """Run every table query against live AACT and write NDJSON + manifest.

    Returns the snapshot directory. Requires ``psycopg`` and a reachable AACT Postgres.
    """
    # --live was exercised against a real hosted AACT snapshot on 2026-06-05 (connection +
    # per-table queries verified). The host serves CTGOV2 uppercased enum codes, which the
    # ratified spec adopts as canonical, so the filter and clean-stage enums accept them
    # directly. Build-time only; never reached at runtime or by an agent.
    logger.warning(
        "--live AACT extraction reaches a real AACT snapshot serving CTGOV2 enum codes "
        "(snapshot_date=%s). Build-time only; never a runtime call.",
        conn.snapshot_date,
    )
    import psycopg  # local import: only needed for a live extraction

    snapshot_dir = out_root / conn.snapshot_date
    snapshot_dir.mkdir(parents=True, exist_ok=True)

    row_counts: dict[str, int] = {}
    aact_version = conn.snapshot_date
    with psycopg.connect(conn.dsn()) as pg:
        # Probe version sources in autocommit so a failing probe rolls back cleanly and
        # never aborts the extract transaction.
        pg.autocommit = True
        for vq in _AACT_VERSION_QUERIES:
            try:
                with pg.cursor() as cur:
                    cur.execute(vq)
                    row = cur.fetchone()
                    if row and row[0]:
                        aact_version = str(row[0])
                        break
            except Exception:  # noqa: BLE001,PERF203 - version is provenance, not critical
                continue

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
        "source": "hosted AACT (CTTI Aggregate Analysis of ClinicalTrials.gov)",
        "source_kind": "hosted live AACT Postgres (NOT a committed sample fixture)",
        "source_url": AACT_SOURCE_URL,
        "snapshot_date": snapshot_date,
        "capture_date": snapshot_date,
        "aact_version": aact_version,
        "build_time_only": True,
        "population_filter": POPULATION_FILTER,
        "enum_format_note": (
            "Hosted AACT serves CTGOV2 uppercased enum codes (study_type='INTERVENTIONAL', "
            "phase='PHASE2', ...), which specs/data.md ('Enum format (CTGOV2)') pins as the "
            "canonical ref_* values. Filter and clean-stage enums use these directly."
        ),
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
