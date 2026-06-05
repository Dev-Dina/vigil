"""Pydantic models for the cleaned ``ref_*`` reference tables (specs/data.md).

``ref_*`` tables are public clinical-trials reference data: RLS-exempt, no PHI, never
sponsor-keyed. Distinct from operational tenant-scoped entities.

Validation happens at the raw->clean boundary. A missing/malformed field raises a typed
``DataValidationError`` (the caller wraps Pydantic's ``ValidationError``) and the offending
record goes to the quality report; it is never silently coerced or dropped.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, computed_field, model_validator

from ingestion.vocab import THERAPEUTIC_AREAS, WITHDRAWAL_REASONS


class StudyType(StrEnum):
    INTERVENTIONAL = "Interventional"


class Phase(StrEnum):
    PHASE1 = "Phase 1"
    PHASE2 = "Phase 2"
    PHASE3 = "Phase 3"
    PHASE4 = "Phase 4"
    EARLY_PHASE1 = "Early Phase 1"
    PHASE1_2 = "Phase 1/Phase 2"
    PHASE2_3 = "Phase 2/Phase 3"
    NA = "N/A"


class Gender(StrEnum):
    ALL = "All"
    FEMALE = "Female"
    MALE = "Male"


class SponsorClass(StrEnum):
    INDUSTRY = "INDUSTRY"
    NIH = "NIH"
    OTHER_GOV = "OTHER_GOV"
    ACADEMIC_OTHER = "ACADEMIC_OTHER"


class Allocation(StrEnum):
    RANDOMIZED = "Randomized"
    NON_RANDOMIZED = "Non-Randomized"
    NA = "N/A"


class InterventionModel(StrEnum):
    PARALLEL = "Parallel Assignment"
    CROSSOVER = "Crossover Assignment"
    SINGLE_GROUP = "Single Group Assignment"
    FACTORIAL = "Factorial Assignment"
    SEQUENTIAL = "Sequential Assignment"
    NA = "N/A"


class Masking(StrEnum):
    NONE = "None (Open Label)"
    SINGLE = "Single"
    DOUBLE = "Double"
    TRIPLE = "Triple"
    QUADRUPLE = "Quadruple"
    NA = "N/A"


class PrimaryPurpose(StrEnum):
    TREATMENT = "Treatment"
    PREVENTION = "Prevention"
    DIAGNOSTIC = "Diagnostic"
    SUPPORTIVE_CARE = "Supportive Care"
    SCREENING = "Screening"
    HEALTH_SERVICES = "Health Services Research"
    BASIC_SCIENCE = "Basic Science"
    DEVICE_FEASIBILITY = "Device Feasibility"
    OTHER = "Other"
    NA = "N/A"


class ArmType(StrEnum):
    EXPERIMENTAL = "Experimental"
    ACTIVE_COMPARATOR = "Active Comparator"
    PLACEBO_COMPARATOR = "Placebo Comparator"
    OTHER = "Other"


# Build a StrEnum dynamically from the controlled vocab so it stays single-sourced.
TherapeuticArea = StrEnum("TherapeuticArea", {v: v for v in THERAPEUTIC_AREAS})
WithdrawalReason = StrEnum("WithdrawalReason", {v: v for v in WITHDRAWAL_REASONS})

NCT_PATTERN = r"^NCT\d{8}$"


class RefTrial(BaseModel):
    """One row per NCT in the modelled cohort."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    nct_id: str = Field(pattern=NCT_PATTERN)
    study_type: StudyType
    phase: Phase
    therapeutic_area: TherapeuticArea
    enrollment: int = Field(gt=0)
    n_arms: int = Field(ge=1)
    n_sites: int = Field(ge=0)
    n_countries: int = Field(ge=0)
    planned_duration_days: int = Field(gt=0)
    actual_duration_days: int | None = None
    allocation: Allocation
    intervention_model: InterventionModel
    masking: Masking
    primary_purpose: PrimaryPurpose
    min_age_years: float | None = None
    max_age_years: float | None = None
    gender: Gender
    healthy_volunteers: bool
    sponsor_class: SponsorClass
    results_reported: bool

    @computed_field  # type: ignore[prop-decorator]
    @property
    def study_url(self) -> str:
        return f"https://clinicaltrials.gov/study/{self.nct_id}"


class RefArm(BaseModel):
    """One row per trial-arm with participant-flow counts."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    arm_id: str
    nct_id: str = Field(pattern=NCT_PATTERN)
    arm_type: ArmType
    started: int = Field(ge=0)
    completed: int = Field(ge=0)
    not_completed: int = Field(ge=0)
    dropout_rate: float

    @model_validator(mode="after")
    def _cross_record(self) -> RefArm:
        if self.completed > self.started:
            raise ValueError(f"completed ({self.completed}) > started ({self.started})")
        if self.not_completed != self.started - self.completed:
            raise ValueError(
                f"not_completed ({self.not_completed}) != started - completed "
                f"({self.started - self.completed})"
            )
        if not (0.0 <= self.dropout_rate <= 1.0):
            raise ValueError(f"dropout_rate {self.dropout_rate} not in [0, 1]")
        if self.started > 0:
            expected = self.not_completed / self.started
            if abs(expected - self.dropout_rate) > 1e-9:
                raise ValueError(
                    f"dropout_rate {self.dropout_rate} != not_completed/started "
                    f"{expected}"
                )
        return self


class RefWithdrawalReason(BaseModel):
    """One row per trial-arm-reason."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    nct_id: str = Field(pattern=NCT_PATTERN)
    arm_id: str
    reason: WithdrawalReason
    count: int = Field(ge=0)
