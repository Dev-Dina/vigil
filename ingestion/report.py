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
    unmapped_enums: list[dict[str, str]] = field(default_factory=list)
    unexplained_withdrawals: list[dict[str, Any]] = field(default_factory=list)
    excluded_censoring: list[dict[str, Any]] = field(default_factory=list)
    spec_contradictions: list[dict[str, Any]] = field(default_factory=list)
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

    def add_excluded_censoring(
        self, nct_id: str, arm_id: str, term: str, count: int
    ) -> None:
        """A right-censoring / still-ongoing withdrawal row excluded from the reason-mix.

        Per specs/data.md censoring is NOT a dropout reason: the row is dropped from
        ``ref_withdrawal_reason`` (neither a category nor OTHER) and recorded here so its
        volume is auditable and never counts toward explained dropout (no silent loss).
        """
        self.excluded_censoring.append(
            {"nct_id": nct_id, "arm_id": arm_id, "term": term, "count": count}
        )

    def add_unmapped_enum(self, nct_id: str, field_name: str, original: str) -> None:
        """A raw enum token outside the spec's controlled set (recorded, never coerced)."""
        self.unmapped_enums.append(
            {"nct_id": nct_id, "field": field_name, "original": original}
        )

    def add_spec_contradiction(
        self, table: str, column: str, spec_says: str, data_shows: str, n_rows: int
    ) -> None:
        """Record a place where the REAL data structurally contradicts specs/data.md."""
        self.spec_contradictions.append(
            {
                "table": table,
                "column": column,
                "spec_says": spec_says,
                "data_shows": data_shows,
                "n_rows_affected": n_rows,
            }
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

    def _excluded_censoring_by_term(self) -> dict[str, int]:
        """Total excluded-censoring volume per normalised term (audit breakdown)."""
        by_term: dict[str, int] = {}
        for row in self.excluded_censoring:
            term = str(row["term"]).strip().lower()
            by_term[term] = by_term.get(term, 0) + int(row["count"])
        return dict(sorted(by_term.items(), key=lambda kv: (-kv[1], kv[0])))

    def to_dict(self) -> dict[str, Any]:
        censoring_volume = sum(int(r["count"]) for r in self.excluded_censoring)
        return {
            "summary": {
                "validation_failures": len(self.validation_failures),
                "unmapped_mesh_terms": len(self.unmapped_mesh),
                "unmapped_reasons": len(self.unmapped_reasons),
                "unmapped_enums": len(self.unmapped_enums),
                "spec_contradictions": len(self.spec_contradictions),
                "arms_with_unexplained_withdrawals": len(self.unexplained_withdrawals),
                "excluded_censoring_rows": len(self.excluded_censoring),
                "excluded_censoring_volume": censoring_volume,
            },
            "spec_contradictions": self.spec_contradictions,
            "table_stats": self.table_stats,
            "validation_failures": self.validation_failures,
            "unmapped_mesh": self.unmapped_mesh,
            "unmapped_reasons": self.unmapped_reasons,
            "unmapped_enums": self.unmapped_enums,
            "unexplained_withdrawals": self.unexplained_withdrawals,
            "excluded_censoring": self.excluded_censoring,
            "excluded_censoring_by_term": self._excluded_censoring_by_term(),
            "notes": self.notes,
        }

    def write(self, path: Path) -> Path:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(self.to_dict(), indent=2, sort_keys=True), encoding="utf-8"
        )
        return path
