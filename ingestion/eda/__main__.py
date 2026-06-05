"""``uv run python -m ingestion.eda`` — run EDA on the real ref_* tables."""

from __future__ import annotations

import sys

from ingestion.eda.report import run_eda


def main() -> int:
    summary = run_eda()
    print("=== Vigil EDA (real AACT cohort) ===")
    print(f"studies post-filter: {summary['study_count_post_filter']:,}")
    print(f"trials with flow:    {summary['trials_with_flow_arms']:,}")
    print(f"overall dropout:     {summary['overall_dropout_rate']}")
    print("figures:")
    for name, path in summary["figures"].items():
        print(f"  - {name}: {path}")
    print("summary: data/eda/eda_summary.json + eda_summary.md")
    return 0


if __name__ == "__main__":
    sys.exit(main())
