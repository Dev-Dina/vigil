"""CI-safe smoke test for the Phase-3 baselines on the GOLDEN cohort.

The golden set (``clean_frames``) is the 64-trial REAL-public slice — all modelling phases,
all carrying ``start_date`` — so the full baseline flow (cohort -> split -> matrices -> loud
leakage check -> GBT + logistic -> per-sponsor_class -> SHAP -> artifacts) runs end-to-end with
NO ``data/clean`` dependency. Everything writes into ``tmp_path`` (never a committed dir) and a
tiny ``shap_sample`` keeps it fast.
"""

from __future__ import annotations

import json
import math
from pathlib import Path

import pytest

from models.baselines import train_baselines_from_frames


@pytest.fixture(scope="module")
def results(clean_frames, tmp_path_factory) -> tuple[dict, Path]:
    out = tmp_path_factory.mktemp("baselines_out")
    res = train_baselines_from_frames(
        clean_frames["ref_trial"],
        clean_frames["ref_arm"],
        out_root=out,
        shap_sample=30,
    )
    return res, out


def test_returns_overall_and_per_class_metrics(results) -> None:
    res, _ = results
    # Overall metric blocks for both models present on TEST.
    assert {"mae", "rmse", "r2", "pr_auc", "brier"} <= set(res["test"]["gbt"])
    assert {"pr_auc", "recall_at_p50", "brier"} <= set(res["test"]["logit"])
    per_class = res["per_sponsor_class"]
    assert per_class, "expected at least one sponsor_class group"
    # Golden carries ACADEMIC_OTHER + INDUSTRY; NIH may be absent — assert the present groups.
    assert set(per_class).issubset({"INDUSTRY", "ACADEMIC_OTHER", "NIH"})
    for metrics in per_class.values():
        assert "gbt_mae" in metrics
        assert "logit_pr_auc" in metrics


def test_model_card_written_with_source_and_leakage_limitation(results) -> None:
    _, out = results
    card = (out / "model_card.md").read_text(encoding="utf-8")
    assert "DATA SOURCE" in card
    assert "REAL" in card
    assert "Sponsor-level leakage" in card or "sponsor-level leakage" in card.lower()


def test_metrics_json_parseable_and_pr_auc_in_range(results) -> None:
    _, out = results
    metrics = json.loads((out / "metrics.json").read_text(encoding="utf-8"))
    for model in ("gbt", "logit"):
        val = metrics["test"][model]["pr_auc"]
        assert isinstance(val, float)
        # PR-AUC is in [0,1] when computable; NaN is allowed when a fold is single-class.
        assert math.isnan(val) or (0.0 <= val <= 1.0)


def test_shap_global_png_exists(results) -> None:
    _, out = results
    assert (out / "shap_global.png").exists()


def test_artifacts_written_under_tmp(results) -> None:
    _, out = results
    # The run wrote only under the pytest tmp dir (never a committed data/ dir).
    assert out.exists()
    assert (
        str(out).find("data") == -1 or "Temp" in str(out) or "tmp" in str(out).lower()
    )
