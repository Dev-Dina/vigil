"""The clean stage must never clobber the REAL ``data/clean`` snapshot on a non-live run.

The real snapshot is the preferred synthetic-generation source and the EDA input. A
fixture/non-live run targeting ``CLEAN_ROOT`` is a bug and must fail LOUD (per CLAUDE.md
"errors never pass silently") rather than overwrite it.
"""

from __future__ import annotations

import pytest

from ingestion.clean import clean_snapshot
from ingestion.config import CLEAN_ROOT
from ingestion.errors import ClobberGuardError
from ingestion.pipeline import run
from ingestion.report import QualityReport


def _existing_parquet_hashes() -> dict[str, bytes]:
    return {p.name: p.read_bytes() for p in CLEAN_ROOT.glob("*.parquet")}


def test_non_live_clean_to_clean_root_raises_and_does_not_write(
    sample_fixture_root,
) -> None:
    before = _existing_parquet_hashes()

    report = QualityReport()
    with pytest.raises(ClobberGuardError) as exc:
        clean_snapshot(
            sample_fixture_root,
            report=report,
            out_root=CLEAN_ROOT,
            live=False,
            fail_loud=True,
        )
    assert str(CLEAN_ROOT) in str(exc.value)
    assert "CLEAN_FIXTURE_ROOT" in str(exc.value)

    # The real snapshot (if any) is byte-identical: the writer never ran.
    assert _existing_parquet_hashes() == before


def test_run_non_live_override_to_clean_root_raises(sample_fixture_root) -> None:
    before = _existing_parquet_hashes()

    with pytest.raises(ClobberGuardError):
        run(live=False, out_clean=CLEAN_ROOT)

    assert _existing_parquet_hashes() == before
