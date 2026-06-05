"""Data-quality report. Nothing is silently lost; it all lands here."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any


@dataclass
class QualityReport:
    """Accumulates validation failures, unmapped originals, and table stats.

    The pipeline writes this as a JSON artifact so dropped/coerced data is auditable.
    """

    validation_failures: list[dict[str, Any]] = field(default_factory=list)
    unmapped_mesh: list[dict[str, str]] = field(default_factory=list)
    unmapped_reasons: list[dict[str, str]] = field(default_factory=list)
    unexplained_withdrawals: list[dict[str, Any]] = field(default_factory=list)
    table_stats: dict[str, dict[str, Any]] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)

    def add_validation_failure(
        self, table: str, nct_id: str | None, field_name: str, message: str, value: Any
    ) -> None:
        self.validation_failures.append(
            {
                "table": table,
                "nct_id": nct_id,
                "field": field_name,
                "message": message,
                "value": repr(value),
            }
        )

    def add_unmapped_mesh(self, nct_id: str, original: str) -> None:
        self.unmapped_mesh.append({"nct_id": nct_id, "original": original})

    def add_unmapped_reason(self, nct_id: str, arm_id: str, original: str) -> None:
        self.unmapped_reasons.append(
            {"nct_id": nct_id, "arm_id": arm_id, "original": original}
        )

    def add_unexplained_withdrawal(
        self, nct_id: str, arm_id: str, not_completed: int, explained: int
    ) -> None:
        self.unexplained_withdrawals.append(
            {
                "nct_id": nct_id,
                "arm_id": arm_id,
                "not_completed": not_completed,
                "explained_by_reasons": explained,
                "unexplained_remainder": not_completed - explained,
            }
        )

    def set_table_stats(
        self, table: str, *, rows: int, null_rates: dict[str, float]
    ) -> None:
        self.table_stats[table] = {"rows": rows, "null_rates": null_rates}

    def to_dict(self) -> dict[str, Any]:
        return {
            "summary": {
                "validation_failures": len(self.validation_failures),
                "unmapped_mesh_terms": len(self.unmapped_mesh),
                "unmapped_reasons": len(self.unmapped_reasons),
                "arms_with_unexplained_withdrawals": len(self.unexplained_withdrawals),
            },
            "table_stats": self.table_stats,
            "validation_failures": self.validation_failures,
            "unmapped_mesh": self.unmapped_mesh,
            "unmapped_reasons": self.unmapped_reasons,
            "unexplained_withdrawals": self.unexplained_withdrawals,
            "notes": self.notes,
        }

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=True), encoding="utf-8"
        )
        return path
