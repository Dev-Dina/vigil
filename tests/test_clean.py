"""Clean-stage cross-record validations and quality-report capture."""

from __future__ import annotations

import copy

import pytest

from ingestion.clean import clean_snapshot
from ingestion.errors import DataValidationError
from ingestion.extract import read_ndjson
from ingestion.report import QualityReport
from ingestion.vocab import normalize_reason


def test_clean_emits_three_ref_tables(clean_frames) -> None:
    assert set(clean_frames) == {"ref_trial", "ref_arm", "ref_withdrawal_reason"}
    assert len(clean_frames["ref_trial"]) > 0
    assert len(clean_frames["ref_arm"]) > 0


def test_clean_cross_record_holds_on_fixture(clean_frames) -> None:
    arms = clean_frames["ref_arm"]
    assert (arms["completed"] <= arms["started"]).all()
    assert (arms["not_completed"] == arms["started"] - arms["completed"]).all()
    assert ((arms["dropout_rate"] >= 0) & (arms["dropout_rate"] <= 1)).all()
    assert (clean_frames["ref_trial"]["enrollment"] > 0).all()
    assert (clean_frames["ref_trial"]["planned_duration_days"] > 0).all()


def test_sum_withdrawals_le_not_completed(clean_frames) -> None:
    arms = clean_frames["ref_arm"].set_index("arm_id")
    wd = clean_frames["ref_withdrawal_reason"].groupby("arm_id")["count"].sum()
    for arm_id, total in wd.items():
        assert total <= arms.loc[arm_id, "not_completed"]


def test_unmapped_reason_recorded_not_dropped(tmp_path, sample_fixture_root) -> None:
    report = QualityReport()
    clean_snapshot(
        sample_fixture_root, report=report, fail_loud=True, out_root=tmp_path
    )
    # The fixture deliberately includes "Unknown other reason" -> OTHER, original logged.
    assert report.unmapped_reasons, "unmapped reasons must be recorded, never lost"
    assert any("unknown" in u["original"].lower() for u in report.unmapped_reasons)


def test_unmapped_reason_normalizes_to_other() -> None:
    canonical, original = normalize_reason("Unknown other reason")
    assert canonical == "OTHER"
    assert original == "Unknown other reason"


def test_known_reason_maps_with_no_loss() -> None:
    canonical, original = normalize_reason("Adverse Event leading to discontinuation")
    assert canonical == "ADVERSE_EVENT"
    assert original is None


def test_inconsistent_milestone_fails_loud(tmp_path, sample_fixture_root) -> None:
    """A milestone where COMPLETED > STARTED must raise, not silently coerce."""
    src = read_ndjson(sample_fixture_root / "milestones.ndjson")
    corrupted = copy.deepcopy(src)
    # Flip one arm's COMPLETED above its STARTED.
    started_rows = {
        (r["nct_id"], r["ctgov_group_code"]): r
        for r in corrupted
        if r["title"] == "STARTED"
    }
    for r in corrupted:
        if r["title"] == "COMPLETED":
            s = started_rows[(r["nct_id"], r["ctgov_group_code"])]["count"]
            r["count"] = s + 50
            break

    snap = tmp_path / "snap"
    snap.mkdir()
    # Copy every table, overriding milestones with the corrupted one.
    import json
    import shutil

    for f in sample_fixture_root.glob("*.ndjson"):
        shutil.copy(f, snap / f.name)
    with (snap / "milestones.ndjson").open("w", encoding="utf-8") as fh:
        for row in corrupted:
            fh.write(json.dumps(row, sort_keys=True) + "\n")

    report = QualityReport()
    with pytest.raises(DataValidationError):
        clean_snapshot(snap, report=report, fail_loud=True, out_root=tmp_path / "out")
