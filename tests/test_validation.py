"""Fail-loud validation and cross-record validation tests (specs/data.md)."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ingestion.schema import RefArm, RefTrial, RefWithdrawalReason


def _valid_trial_fields() -> dict:
    return {
        "nct_id": "NCT00000001",
        "study_type": "INTERVENTIONAL",
        "phase": "PHASE3",
        "therapeutic_area": "ONCOLOGY",
        "enrollment": 200,
        "n_arms": 2,
        "n_sites": 10,
        "n_countries": 3,
        "planned_duration_days": 365,
        "actual_duration_days": 400,
        "allocation": "RANDOMIZED",
        "intervention_model": "PARALLEL",
        "masking": "DOUBLE",
        "primary_purpose": "TREATMENT",
        "min_age_years": 18.0,
        "max_age_years": 75.0,
        "gender": "ALL",
        "healthy_volunteers": False,
        "sponsor_class": "INDUSTRY",
        "results_reported": True,
    }


def test_ref_trial_happy_path() -> None:
    trial = RefTrial(**_valid_trial_fields())
    assert trial.study_url == "https://clinicaltrials.gov/study/NCT00000001"


def test_enrollment_must_be_positive() -> None:
    fields = _valid_trial_fields() | {"enrollment": 0}
    with pytest.raises(ValidationError):
        RefTrial(**fields)


def test_planned_duration_must_be_positive() -> None:
    fields = _valid_trial_fields() | {"planned_duration_days": 0}
    with pytest.raises(ValidationError):
        RefTrial(**fields)


def test_bad_nct_id_pattern_fails_loud() -> None:
    fields = _valid_trial_fields() | {"nct_id": "00000001"}
    with pytest.raises(ValidationError):
        RefTrial(**fields)


def test_unknown_enum_value_fails_loud() -> None:
    fields = _valid_trial_fields() | {"sponsor_class": "MYSTERY"}
    with pytest.raises(ValidationError):
        RefTrial(**fields)


def _arm(**over) -> dict:
    base = {
        "arm_id": "NCT00000001P0",
        "nct_id": "NCT00000001",
        "arm_type": "Experimental",
        "started": 100,
        "completed": 80,
        "not_completed": 20,
        "dropout_rate": 0.2,
    }
    return base | over


def test_arm_happy_path() -> None:
    RefArm(**_arm())


def test_completed_gt_started_fails_loud() -> None:
    with pytest.raises(ValidationError, match="completed"):
        RefArm(**_arm(started=50, completed=80, not_completed=-30, dropout_rate=0.0))


def test_not_completed_identity_enforced() -> None:
    with pytest.raises(ValidationError, match="not_completed"):
        RefArm(**_arm(not_completed=10))


def test_dropout_rate_out_of_range_fails_loud() -> None:
    # not_completed/started identity holds but rate is inconsistent / out of [0,1].
    with pytest.raises(ValidationError):
        RefArm(**_arm(dropout_rate=1.5))


def test_withdrawal_reason_enum_enforced() -> None:
    with pytest.raises(ValidationError):
        RefWithdrawalReason(
            nct_id="NCT00000001",
            arm_id="NCT00000001P0",
            reason="MADE_UP",
            count=3,
        )


def test_withdrawal_count_non_negative() -> None:
    with pytest.raises(ValidationError):
        RefWithdrawalReason(
            nct_id="NCT00000001",
            arm_id="NCT00000001P0",
            reason="ADVERSE_EVENT",
            count=-1,
        )
