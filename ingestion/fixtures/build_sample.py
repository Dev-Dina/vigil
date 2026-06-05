"""Generate the SAMPLE AACT NDJSON fixture (NOT a real extract, never committed).

Deterministic: a fixed seed produces a small, realistic interventional-with-flow cohort so
the clean->synthetic->features pipeline is runnable and testable end-to-end offline. Run:

    uv run python -m ingestion.fixtures.build_sample

Real provenance comes from ``ingestion.extract`` against a live AACT snapshot; this only
exists so the rest of the pipeline does not depend on network access.
"""

from __future__ import annotations

import json
from datetime import date, timedelta
from pathlib import Path

import numpy as np

from ingestion.extract import write_manifest

SAMPLE_DIR = Path(__file__).resolve().parent / "aact_sample"
SAMPLE_SEED = 7
N_TRIALS = 60

_PHASES = ["PHASE1", "PHASE2", "PHASE3", "PHASE4"]
_AREAS = [
    ("Breast Neoplasms", "ONCOLOGY"),
    ("Heart Failure", "CARDIOVASCULAR"),
    ("Alzheimer Disease", "NEUROLOGY"),
    ("Major Depressive Disorder", "PSYCHIATRY"),
    ("HIV Infections", "INFECTIOUS_DISEASE"),
    ("Diabetes Mellitus, Type 2", "ENDOCRINE_METABOLIC"),
    ("Asthma", "RESPIRATORY"),
    ("Rheumatoid Arthritis", "IMMUNOLOGY_RHEUMATOLOGY"),
]
_SPONSOR_CLASSES = ["INDUSTRY", "NIH", "FED", "OTHER"]
_MASKINGS = ["NONE", "DOUBLE", "SINGLE", "QUADRUPLE"]
_REASONS = [
    "Adverse Event",
    "Lack of Efficacy",
    "Withdrawal by Subject",
    "Lost to Follow-up",
    "Physician Decision",
    "Protocol Violation",
    "Death",
    "Pregnancy",
    "Non-compliance",
    "Study terminated by sponsor",  # -> STUDY_TERMINATED
    "Did not complete mid-study survey",  # -> ADMINISTRATIVE
    "Unknown other reason",  # falls to OTHER on purpose
]

# Censoring / still-ongoing terms exercised as a separate, dropped-from-reason-mix path: they
# must be recorded as excluded-censoring, never as a category nor OTHER (specs/data.md).
_CENSORING_REASONS = [
    "Participants entered open label period",
    "Ongoing",
]


def build(out_dir: Path | None = None) -> Path:
    """Deterministically write the SAMPLE fixture.

    ``out_dir`` defaults to the canonical (git-ignored) ``aact_sample`` directory; tests
    pass a tmp path so the suite never depends on a committed fixture. Same seed -> identical
    bytes regardless of destination.
    """
    target = SAMPLE_DIR if out_dir is None else Path(out_dir)
    rng = np.random.default_rng(SAMPLE_SEED)
    target.mkdir(parents=True, exist_ok=True)

    tables: dict[str, list[dict]] = {
        "studies": [],
        "calculated_values": [],
        "eligibilities": [],
        "designs": [],
        "browse_conditions": [],
        "conditions": [],
        "interventions": [],
        "sponsors": [],
        "countries": [],
        "facilities": [],
        "result_groups": [],
        "milestones": [],
        "drop_withdrawals": [],
    }

    for i in range(N_TRIALS):
        nct_id = f"NCT{10000000 + i:08d}"
        phase = _PHASES[int(rng.integers(0, len(_PHASES)))]
        mesh, _area = _AREAS[int(rng.integers(0, len(_AREAS)))]
        sponsor_class = _SPONSOR_CLASSES[int(rng.integers(0, len(_SPONSOR_CLASSES)))]
        masking = _MASKINGS[int(rng.integers(0, len(_MASKINGS)))]
        n_arms = int(rng.integers(1, 4))
        enrollment = int(rng.integers(40, 600))
        start = date(2015, 1, 1) + timedelta(days=int(rng.integers(0, 2000)))
        duration_days = int(rng.integers(180, 1500))
        pcd = start + timedelta(days=duration_days)
        completion = pcd + timedelta(days=int(rng.integers(0, 200)))
        n_sites = int(rng.integers(1, 40))

        tables["studies"].append(
            {
                "nct_id": nct_id,
                "study_type": "INTERVENTIONAL",
                "overall_status": "Completed",
                "phase": phase,
                "enrollment": enrollment,
                "enrollment_type": "Actual",
                "start_date": start.isoformat(),
                "primary_completion_date": pcd.isoformat(),
                "completion_date": completion.isoformat(),
                "number_of_arms": n_arms,
            }
        )
        tables["calculated_values"].append(
            {
                "nct_id": nct_id,
                "actual_duration": int(
                    rng.integers(duration_days, duration_days + 200)
                ),
                "number_of_facilities": n_sites,
                "were_results_reported": True,
                "minimum_age_num": float(int(rng.integers(18, 40))),
                "maximum_age_num": float(int(rng.integers(60, 85))),
            }
        )
        tables["eligibilities"].append(
            {
                "nct_id": nct_id,
                "gender": ["ALL", "FEMALE", "MALE"][int(rng.integers(0, 3))],
                "minimum_age": "18 Years",
                "maximum_age": "85 Years",
                "healthy_volunteers": "No"
                if rng.random() > 0.2
                else "Accepts Healthy Volunteers",
                "sampling_method": None,
            }
        )
        tables["designs"].append(
            {
                "nct_id": nct_id,
                "allocation": "RANDOMIZED" if n_arms > 1 else "NA",
                "intervention_model": "PARALLEL" if n_arms > 1 else "SINGLE_GROUP",
                "primary_purpose": "TREATMENT",
                "masking": masking,
            }
        )
        tables["browse_conditions"].append({"nct_id": nct_id, "mesh_term": mesh})
        tables["conditions"].append({"nct_id": nct_id, "downcase_name": mesh.lower()})
        tables["interventions"].append(
            {"nct_id": nct_id, "intervention_type": "Drug", "name": f"Drug-{i}"}
        )
        tables["sponsors"].append(
            {
                "nct_id": nct_id,
                "agency_class": sponsor_class,
                "lead_or_collaborator": "lead",
            }
        )
        n_countries = int(rng.integers(1, 6))
        for ci in range(n_countries):
            tables["countries"].append(
                {"nct_id": nct_id, "name": f"Country{ci}", "removed": False}
            )
        for fi in range(n_sites):
            tables["facilities"].append(
                {"nct_id": nct_id, "country": f"Country{fi % n_countries}"}
            )

        # Arms with participant flow. Dropout calibrated to realistic levels that vary
        # with phase / blinding / multi-site so synthetic calibration has real signal.
        base = 0.18 + 0.03 * _PHASES.index(phase)
        if masking == "NONE":
            base += 0.04
        if n_sites > 10:
            base += 0.03
        per_arm = max(10, enrollment // n_arms)
        for a in range(n_arms):
            code = f"P{a}"
            rate = float(np.clip(base + rng.normal(0, 0.03), 0.02, 0.6))
            started = per_arm
            not_completed = int(round(started * rate))
            completed = started - not_completed
            title = (
                "Experimental: Drug"
                if a == 0
                else ("Placebo Comparator" if a == 1 else "Active Comparator")
            )
            tables["result_groups"].append(
                {
                    "id": i * 10 + a,
                    "nct_id": nct_id,
                    "ctgov_group_code": code,
                    "result_type": "Participant Flow",
                    "title": title,
                }
            )
            tables["milestones"].append(
                {
                    "nct_id": nct_id,
                    "result_group_id": i * 10 + a,
                    "ctgov_group_code": code,
                    "title": "STARTED",
                    "period": "Overall Study",
                    "count": started,
                }
            )
            tables["milestones"].append(
                {
                    "nct_id": nct_id,
                    "result_group_id": i * 10 + a,
                    "ctgov_group_code": code,
                    "title": "COMPLETED",
                    "period": "Overall Study",
                    "count": completed,
                }
            )
            tables["milestones"].append(
                {
                    "nct_id": nct_id,
                    "result_group_id": i * 10 + a,
                    "ctgov_group_code": code,
                    "title": "NOT COMPLETED",
                    "period": "Overall Study",
                    "count": not_completed,
                }
            )
            # Distribute not_completed across reasons (leaving a small unexplained
            # remainder, which the report captures).
            remaining = not_completed
            probs = rng.dirichlet(np.ones(len(_REASONS)))
            for ri, reason in enumerate(_REASONS):
                if remaining <= 0:
                    break
                cnt = int(round(not_completed * probs[ri]))
                cnt = min(cnt, remaining)
                if cnt > 0:
                    tables["drop_withdrawals"].append(
                        {
                            "nct_id": nct_id,
                            "result_group_id": i * 10 + a,
                            "ctgov_group_code": code,
                            "period": "Overall Study",
                            "reason": reason,
                            "count": cnt,
                        }
                    )
                    remaining -= cnt
            # Add censoring rows on a deterministic subset of arms. These are NOT dropout
            # reasons: the clean stage must exclude them from ref_withdrawal_reason and record
            # them as excluded-censoring. Their count is independent of not_completed so they
            # never affect the cross-record check or arm-level counts.
            if a == 0:
                censor_reason = _CENSORING_REASONS[i % len(_CENSORING_REASONS)]
                tables["drop_withdrawals"].append(
                    {
                        "nct_id": nct_id,
                        "result_group_id": i * 10 + a,
                        "ctgov_group_code": code,
                        "period": "Overall Study",
                        "reason": censor_reason,
                        "count": int(rng.integers(1, 6)),
                    }
                )

    row_counts = {}
    for table, rows in tables.items():
        path = target / f"{table}.ndjson"
        with path.open("w", encoding="utf-8") as fh:
            for row in rows:
                fh.write(json.dumps(row, sort_keys=True))
                fh.write("\n")
        row_counts[table] = len(rows)

    manifest_path = write_manifest(
        target,
        snapshot_date="2024-06-01-SAMPLE",
        aact_version="SAMPLE FIXTURE - NOT A REAL AACT EXTRACT",
        row_counts=row_counts,
    )
    # Stamp unmistakable non-real flags into the manifest so this fixture can never be
    # mistaken for a real AACT extract by any downstream reader.
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["sample"] = True
    manifest["synthetic"] = True
    manifest["is_real_aact"] = False
    manifest["disclaimer"] = (
        "SAMPLE FIXTURE — deterministically generated, NOT a real ClinicalTrials.gov/AACT "
        "extract. Do not treat as real clinical data."
    )
    manifest_path.write_text(
        json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
    )
    (target / "README.md").write_text(
        "# SAMPLE AACT fixture\n\n"
        "NOT a real ClinicalTrials.gov/AACT extract. Deterministically generated by "
        "`ingestion/fixtures/build_sample.py` so the clean -> synthetic -> features "
        "pipeline runs and is testable offline. Do not treat as real clinical data.\n",
        encoding="utf-8",
    )
    return target


if __name__ == "__main__":
    out = build()
    print(f"Wrote sample fixture to {out}")
