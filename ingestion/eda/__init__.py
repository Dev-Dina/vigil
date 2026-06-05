"""Exploratory data analysis over the REAL ``ref_*`` reference tables.

Build-time only, run after raw->clean. Reads the validated Parquet under ``data/clean/`` and
writes figures (PNG) plus a markdown and JSON summary under ``data/eda/``. No PHI, no live
calls, never reached at runtime. Invoke with ``uv run python -m ingestion.eda``.
"""

from __future__ import annotations

__all__ = ["run_eda"]

from ingestion.eda.report import run_eda
