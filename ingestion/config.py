"""Pipeline configuration. Connection details come from env, never hardcoded."""

from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path

# Repo-root-relative output roots. ``data/`` is git-ignored; extracts never committed.
REPO_ROOT = Path(__file__).resolve().parent.parent
DATA_ROOT = REPO_ROOT / "data"
RAW_ROOT = DATA_ROOT / "raw" / "aact"
CLEAN_ROOT = DATA_ROOT / "clean"
# A non-live (fixture) pipeline run cleans here so it never clobbers a real ``data/clean``
# snapshot (which is the preferred synthetic-generation source and the EDA input).
CLEAN_FIXTURE_ROOT = DATA_ROOT / "clean_fixture"
SYNTHETIC_ROOT = DATA_ROOT / "synthetic"
REPORT_ROOT = DATA_ROOT / "reports"
# A non-live run writes its reports here so it never clobbers the REAL reports
# (the quality/calibration artifacts describing the real cohort — EDA + audit inputs).
REPORT_FIXTURE_ROOT = DATA_ROOT / "reports_fixture"

# The committed ingestion GOLDEN SET: a frozen slice of REAL PUBLIC AACT trial-level data
# (raw NDJSON) committed alongside its expected cleaned ``ref_*`` output. It is the
# clean-transform oracle (raw -> ref_*) AND the substrate for a non-live pipeline run, so the
# pipeline is runnable end-to-end without a live AACT Postgres. NO PHI, NO synthetic.
GOLDEN_ROOT = REPO_ROOT / "tests" / "golden"
GOLDEN_RAW_ROOT = GOLDEN_ROOT / "raw"

AACT_SOURCE_URL = "https://aact.ctti-clinicaltrials.org/"

# Default prediction horizon in days for the dropout label.
DEFAULT_HORIZON_DAYS = 28


@dataclass(frozen=True)
class AactConnection:
    """AACT Postgres connection, sourced from the environment.

    AACT publishes monthly static snapshots; the snapshot date is the provenance key.
    """

    host: str
    port: int
    dbname: str
    user: str
    password: str
    snapshot_date: str  # YYYY-MM-DD, the pinned monthly snapshot

    @classmethod
    def from_env(cls) -> AactConnection:
        def _req(key: str) -> str:
            val = os.environ.get(key)
            if not val:
                raise RuntimeError(
                    f"Missing required env var {key}. AACT connection details must come "
                    f"from the environment, never hardcoded."
                )
            return val

        return cls(
            host=_req("AACT_HOST"),
            port=int(os.environ.get("AACT_PORT", "5432")),
            dbname=_req("AACT_DB"),
            user=_req("AACT_USER"),
            password=_req("AACT_PASSWORD"),
            snapshot_date=_req("AACT_SNAPSHOT_DATE"),
        )

    def dsn(self) -> str:
        return (
            f"host={self.host} port={self.port} dbname={self.dbname} "
            f"user={self.user} password={self.password}"
        )
