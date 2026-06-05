"""Vigil build-time data pipeline (Phase 1).

Build-time ingestion of ClinicalTrials.gov / AACT, cleaning to validated ``ref_*``
reference tables, and generation of a clearly-labelled synthetic per-participant cohort
calibrated to the real aggregate statistics. Never reached at runtime, never by an agent.

See ``/specs/data.md`` for the authoritative contract.
"""

from __future__ import annotations

__all__ = ["SYNTHETIC_SEED"]

# Single fixed seed for the synthetic cohort. Recorded in the synthetic manifest.
# Regeneration with this seed is bit-identical.
SYNTHETIC_SEED = 20240601
