"""Clean-stage cross-record validations and quality-report capture."""

from __future__ import annotations

import copy

import pytest

from ingestion.clean import clean_snapshot
from ingestion.errors import DataValidationError
from ingestion.extract import read_ndjson
from ingestion.report import QualityReport
from ingestion.vocab import CENSORED, normalize_reason


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


def test_study_terminated_maps() -> None:
    for text in (
        "Study terminated by sponsor",
        "DSMB design modification",
        "Sponsor decision",
        "Study halted",
    ):
        canonical, original = normalize_reason(text)
        assert canonical == "STUDY_TERMINATED", text
        assert original is None


def test_administrative_maps() -> None:
    for text in (
        "Did not complete mid-study survey",
        "Did not complete any study assessment",
        "Withdrawal information not recorded",
        "Administrative",
    ):
        canonical, original = normalize_reason(text)
        assert canonical == "ADMINISTRATIVE", text
        assert original is None


def test_censoring_is_excluded_not_a_reason() -> None:
    """Censoring/ongoing returns the CENSORED sentinel with the original — never a category."""
    for text in (
        "Participants entered open label period",
        "Ongoing",
        "Still on study",
        "Option to switch to a rollover study",
    ):
        canonical, original = normalize_reason(text)
        assert canonical == CENSORED, text
        assert original == text


def test_censoring_excluded_from_withdrawal_table(
    tmp_path, sample_fixture_root
) -> None:
    report = QualityReport()
    frames = clean_snapshot(
        sample_fixture_root, report=report, fail_loud=True, out_root=tmp_path
    )
    # Recorded separately, never lost.
    assert report.excluded_censoring, (
        "censoring rows must be recorded as excluded-censoring"
    )
    assert sum(r["count"] for r in report.excluded_censoring) > 0
    # Never a withdrawal-reason category and never OTHER-via-censoring text.
    reasons = set(frames["ref_withdrawal_reason"]["reason"].unique())
    assert CENSORED not in reasons
    censor_terms = {r["term"].lower() for r in report.excluded_censoring}
    assert any("open label" in t or "ongoing" in t for t in censor_terms)


def test_new_categories_present_in_withdrawal_table(
    tmp_path, sample_fixture_root
) -> None:
    report = QualityReport()
    frames = clean_snapshot(
        sample_fixture_root, report=report, fail_loud=True, out_root=tmp_path
    )
    reasons = set(frames["ref_withdrawal_reason"]["reason"].unique())
    assert "STUDY_TERMINATED" in reasons
    assert "ADMINISTRATIVE" in reasons


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
