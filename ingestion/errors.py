"""Typed errors for the data pipeline. Validation fails LOUD."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class DataValidationError(Exception):
    """Raised when a raw->clean record is missing or malformed.

    Carries enough structured context to write a quality-report row. Never caught to
    silently coerce or drop a record; the pipeline records it and (per spec) fails loud.
    """

    table: str
    nct_id: str | None
    field_name: str
    message: str
    value: Any = None
    record: dict[str, Any] = field(default_factory=dict)

    def __str__(self) -> str:  # pragma: no cover - trivial
        return (
            f"[{self.table}] nct_id={self.nct_id} field={self.field_name}: "
            f"{self.message} (value={self.value!r})"
        )


class CalibrationError(Exception):
    """Raised when a synthetic calibration target falls outside tolerance."""


class LeakageError(Exception):
    """Raised when a feature-pipeline leakage invariant is violated."""


class ClobberGuardError(Exception):
    """Raised when a non-live clean stage would overwrite the REAL clean snapshot.

    The real ``data/clean`` snapshot is the preferred synthetic-generation source and the
    EDA input; a fixture/non-live run must never clobber it. A non-live run must target
    ``CLEAN_FIXTURE_ROOT`` instead.
    """
