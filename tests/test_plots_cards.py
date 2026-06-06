"""Tests for build-time model-card and plot helpers."""

from __future__ import annotations

from pathlib import Path

import numpy as np

from models.cards.template import render_model_card
from models.plots import (
    plot_calibration_curve,
    plot_metric_by_group,
    plot_pr_curve,
    plot_residuals,
)


def test_render_model_card_includes_source_and_group_table() -> None:
    card = render_model_card(
        {
            "model_name": "Retention Risk",
            "model_type": "Gradient boosted classifier",
            "data_source": "SYNTHETIC cohort",
            "cohort_description": "Synthetic participant retention cohort",
            "target": "participant dropout",
            "features": ["age_bucket", "visit_count"],
            "metrics": {
                "auc_pr": 0.61,
                "per_sponsor_class": {
                    "Industry": {"auc_pr": 0.58, "brier": 0.19},
                    "NIH": {"auc_pr": 0.66, "brier": 0.17},
                },
            },
            "calibration_note": "Calibrated on validation folds.",
            "limitations": ["Not approved for clinical decision-making."],
        }
    )

    assert "**DATA SOURCE: SYNTHETIC cohort**" in card
    assert "## LIMITATIONS" in card
    assert "| sponsor_class | auc_pr | brier |" in card
    assert "| Industry | 0.58 | 0.19 |" in card
    assert "| NIH | 0.66 | 0.17 |" in card


def test_plot_helpers_write_non_empty_pngs(tmp_path: Path) -> None:
    y_true = np.array([0, 1, 0, 1, 1, 0], dtype=float)
    y_score = np.array([0.05, 0.8, 0.3, 0.7, 0.9, 0.2], dtype=float)
    y_pred = np.array([1.0, 2.8, 2.4, 3.9, 5.1, 5.7], dtype=float)
    y_reg = np.array([1.1, 2.6, 2.5, 4.2, 4.8, 6.0], dtype=float)

    outputs = [
        plot_pr_curve(y_true, y_score, tmp_path / "pr.png"),
        plot_calibration_curve(y_true, y_score, tmp_path / "calibration.png"),
        plot_residuals(y_reg, y_pred, tmp_path / "residuals.png"),
        plot_metric_by_group(
            {"Industry": 0.58, "NIH": 0.66},
            tmp_path / "metric_by_group.png",
            "AUC PR",
        ),
    ]

    for output in outputs:
        assert output.exists()
        assert output.stat().st_size > 0
        assert output.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
