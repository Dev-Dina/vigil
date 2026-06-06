"""A non-live run must never clobber the REAL ``data/clean`` snapshot or the REAL
``data/reports`` — both describe the real cohort (synthetic-generation source, EDA + audit
inputs). A fixture/non-live run targeting them is a bug and must fail LOUD (per CLAUDE.md
"errors never pass silently") rather than overwrite them.
"""

from __future__ import annotations

import pytest

from ingestion.clean import clean_snapshot
from ingestion.config import CLEAN_ROOT, REPORT_FIXTURE_ROOT, REPORT_ROOT
from ingestion.errors import ClobberGuardError
from ingestion.pipeline import _resolve_report_root, run
from ingestion.report import QualityReport


def _existing_parquet_hashes() -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in CLEAN_ROOT.glob("*.parquet")}


def _existing_report_hashes() -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in REPORT_ROOT.glob("*.json")}


def test_non_live_clean_to_clean_root_raises_and_does_not_write(
    golden_raw_root,
) -> None:
    before = _existing_parquet_hashes()

    report = QualityReport()
    with pytest.raises(ClobberGuardError) as exc:
        clean_snapshot(
            golden_raw_root,
            report=report,
            out_root=CLEAN_ROOT,
            live=False,
            fail_loud=True,
        )
    assert str(CLEAN_ROOT) in str(exc.value)
    assert "CLEAN_FIXTURE_ROOT" in str(exc.value)

    # The real snapshot (if any) is byte-identical: the writer never ran.
    assert _existing_parquet_hashes() == before


def test_run_non_live_override_to_clean_root_raises(golden_raw_root) -> None:
    before = _existing_parquet_hashes()

    with pytest.raises(ClobberGuardError):
        run(live=False, out_clean=CLEAN_ROOT)

    assert _existing_parquet_hashes() == before


def test_run_non_live_override_to_report_root_raises_and_leaves_reports_untouched() -> (
    None
):
    """A non-live run pointed at the REAL reports dir fails loud and writes nothing there."""
    before = _existing_report_hashes()

    with pytest.raises(ClobberGuardError) as exc:
        run(live=False, out_reports=REPORT_ROOT)

    assert str(REPORT_ROOT) in str(exc.value)
    assert "REPORT_FIXTURE_ROOT" in str(exc.value)
    # The real reports (if any) are byte-identical: nothing was overwritten.
    assert _existing_report_hashes() == before


def test_report_root_resolution_protects_real_reports() -> None:
    """The single resolver: non-live defaults to the fixture dir and refuses REPORT_ROOT."""
    # Default non-live run writes to the fixture reports dir, never the real one.
    assert _resolve_report_root(live=False, out_reports=None) == REPORT_FIXTURE_ROOT
    # A live run uses the real reports dir.
    assert _resolve_report_root(live=True, out_reports=None) == REPORT_ROOT
    # An explicit non-live override at REPORT_ROOT fails loud.
    with pytest.raises(ClobberGuardError):
        _resolve_report_root(live=False, out_reports=REPORT_ROOT)
