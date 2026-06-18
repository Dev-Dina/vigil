"""Gate 9.7a — champion LSTM output calibration (isotonic, val-fold).

Proves the honesty contract of calibrating the champion's compressed sigmoid outputs so the
operational ``> 0.6`` HIGH band is reachable by the REAL model (no override):

- DISCRIMINATION UNCHANGED (the key honesty test): calibration is monotonic, so test ROC-AUC is
  invariant pre/post and the raw per-decision-point PR-AUC reproduces the trained bar. Calibration
  re-maps the probability scale; it does NOT manufacture discrimination.
- LEAKAGE-SAFE: the calibrator is fit on the held-out VAL fold, frozen in the artifact and
  asserted DISJOINT from both the LSTM's TRAIN fold and the reported TEST fold; it uses no outcome
  at score time.
- REACHABILITY: the calibrated real LSTM exceeds 0.6 on a heavy-disengagement trajectory (raw was
  <= 0.6 — calibration UNLOCKED the band), and drives a genuine serious-risk crossing end-to-end
  through the worker with NO scorer override.
- Calibration QUALITY reported honestly (ECE does not worsen); attribution stays real +
  leakage-safe on the calibrated champion.

Marked slow (torch) like the other B2b/B2d LSTM tests so torch never leaks into the
fast / check_specs / golden gates. Requires: live Postgres, data/models/t2d/sequence_v1.1_demo.pt.
"""

from __future__ import annotations

import asyncio
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

pytestmark = pytest.mark.slow

_ARTIFACT_PATH = Path("data/models/t2d/sequence_v1.1_demo.pt")
_CHAMPION_MV = "sequence_v1.1:demo"
_RAW_PR_AUC = 0.3390398896276865  # trained bar (RAW ranking; unchanged by calibration)
_PR_AUC_TOL = 0.001
_ROC_INVARIANCE_TOL = 1e-3
_SERIOUS_THRESHOLD = 0.6


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _load_artifact() -> dict:
    import torch

    return torch.load(str(_ARTIFACT_PATH), weights_only=False)


def _test_fold_raw_probs(artifact: dict) -> tuple[np.ndarray, np.ndarray]:
    """HERMETIC: raw per-decision-point (y, prob) on the artifact's frozen test fold."""
    from models.t2d.sequence import (
        LSTMClassifier,
        _decision_point_eval,
        _predict_steps,
        build_tensors,
    )
    from models.t2d.synthetic_data import load_synthetic_t2d

    model = LSTMClassifier(artifact["seq_dim"], artifact["static_dim"], artifact["cfg"])
    model.load_state_dict(artifact["state_dict"])
    model.eval()

    synth = load_synthetic_t2d()
    test_t = build_tensors(
        synth,
        artifact["static_enc"],
        frozenset(artifact["test_nct_ids"]),
        cfg=artifact["cfg"],
        seq_means=artifact["seq_means"],
        seq_stds=artifact["seq_stds"],
    )
    probs = _predict_steps(model, test_t, batch_size=artifact["cfg"].batch_size)
    y, pr, _ = _decision_point_eval(test_t, probs)
    return y, pr


def _static_row(pid: str) -> dict:
    return {
        "participant_id": pid,
        "age_years": 65.0,
        "hba1c_pct": 7.5,
        "bmi": 28.0,
        "sex": "Male",
        "arm_type": "Placebo Comparator",
        "n_sites": 3,
        "planned_duration_days": 365,
        "phase": "PHASE3",
    }


def _heavy_trajectory(pid: str, *, n_missed: int = 10) -> pd.DataFrame:
    """One attended visit then ``n_missed`` consecutive missed visits (heavy disengagement)."""
    pattern = [False] + [True] * n_missed
    rows = []
    cum = cons = 0
    for vi, missed in enumerate(pattern):
        if missed:
            cum += 1
            cons += 1
        else:
            cons = 0
        rows.append(
            {
                "participant_id": pid,
                "visit_index": vi,
                "attended": not missed,
                "missed": missed,
                "cumulative_missed": cum,
                "consecutive_missed": cons,
            }
        )
    return pd.DataFrame(rows)


# ---------------------------------------------------------------------------
# 1. The artifact carries the calibrator + frozen folds (hermetic)
# ---------------------------------------------------------------------------


def test_artifact_carries_calibrator_and_frozen_folds() -> None:
    art = _load_artifact()
    assert art.get("model_version") == _CHAMPION_MV
    cal = art.get("calibration")
    assert cal is not None, "v1.1 artifact must carry an output calibrator (Gate 9.7a)"
    assert cal["method"] == "isotonic"
    assert cal["fit_split"] == "val"
    x = np.asarray(cal["x"], dtype=float)
    y = np.asarray(cal["y"], dtype=float)
    assert x.size >= 2 and y.size == x.size
    # monotone non-decreasing knots (a valid isotonic map => ranking-preserving)
    assert np.all(np.diff(x) >= 0), "calibrator x-knots must be ascending"
    assert np.all(np.diff(y) >= -1e-12), "calibrator y-knots must be non-decreasing"
    for key in ("train_nct_ids", "val_nct_ids", "test_nct_ids"):
        assert art.get(key), f"artifact must freeze {key}"


# ---------------------------------------------------------------------------
# 2. LEAKAGE: the calibration fit split is disjoint from train AND test
# ---------------------------------------------------------------------------


def test_calibration_fit_split_disjoint_from_train_and_test() -> None:
    """The calibrator is fit on VAL; VAL must be disjoint from the LSTM's TRAIN fold AND the
    reported TEST fold. Asserted hermetically from the frozen NCT-id folds (no PHI)."""
    art = _load_artifact()
    train = frozenset(art["train_nct_ids"])
    val = frozenset(art["val_nct_ids"])
    test = frozenset(art["test_nct_ids"])

    assert art["calibration"]["fit_split"] == "val"
    assert val, "calibration fit split (val) is empty"
    assert val.isdisjoint(test), (
        "LEAK: calibration fit fold overlaps the reported TEST fold"
    )
    assert val.isdisjoint(train), (
        "LEAK: calibration fit fold overlaps the LSTM's TRAIN fold"
    )
    assert train.isdisjoint(test), "LEAK: train fold overlaps test fold"


# ---------------------------------------------------------------------------
# 3. DISCRIMINATION UNCHANGED (the key honesty test)
# ---------------------------------------------------------------------------


def test_discrimination_unchanged_pre_post_calibration() -> None:
    from sklearn.metrics import roc_auc_score

    from models.t2d.calibration import apply_calibration
    from models.t2d.metrics import classifier_panel

    art = _load_artifact()
    y, pr = _test_fold_raw_probs(art)

    # raw PR-AUC reproduces the trained bar (calibration does not touch the raw ranking).
    raw_pr_auc = classifier_panel(y, pr)["pr_auc"]
    assert abs(raw_pr_auc - _RAW_PR_AUC) <= _PR_AUC_TOL, (
        f"raw PR-AUC {raw_pr_auc:.4f} drifted from trained {_RAW_PR_AUC:.4f}"
    )

    cal = apply_calibration(pr, art["calibration"])
    roc_raw = roc_auc_score(y, pr)
    roc_cal = roc_auc_score(y, cal)
    assert abs(roc_cal - roc_raw) < _ROC_INVARIANCE_TOL, (
        f"calibration moved ROC-AUC by {abs(roc_cal - roc_raw):.4e} — monotonic calibration "
        "MUST preserve ranking; a non-calibration change leaked in"
    )

    # ranking provably preserved: calibrated scores are non-decreasing along the raw-score order.
    order = np.argsort(pr, kind="mergesort")
    assert np.all(np.diff(cal[order]) >= -1e-12), (
        "calibration reordered the scores — discrimination is not preserved"
    )


# ---------------------------------------------------------------------------
# 4. REACHABILITY: the calibrated real LSTM exceeds 0.6 (calibration unlocked it)
# ---------------------------------------------------------------------------


def test_calibration_unlocks_high_band_no_override() -> None:
    from vigil.workers.tasks import LSTMScorer

    art = _load_artifact()
    pid = "11111111-1111-1111-1111-111111111111"
    static = pd.DataFrame([_static_row(pid)])
    eng = _heavy_trajectory(pid)

    calibrated = LSTMScorer(art)(static, eng)[0]

    raw_scorer = LSTMScorer(art)
    raw_scorer._calibration = None  # identity => the model's RAW (compressed) output
    raw = raw_scorer(static, eng)[0]

    assert raw <= _SERIOUS_THRESHOLD, (
        f"raw score {raw:.4f} already exceeded {_SERIOUS_THRESHOLD} — the band was not "
        "compressed; this test no longer demonstrates the calibration unlock"
    )
    assert calibrated > _SERIOUS_THRESHOLD, (
        f"calibrated score {calibrated:.4f} does NOT exceed {_SERIOUS_THRESHOLD} — the serious "
        "crossing is not reachable by the real calibrated model"
    )


# ---------------------------------------------------------------------------
# 5. Calibration QUALITY reported honestly (does not worsen)
# ---------------------------------------------------------------------------


def test_calibration_quality_does_not_worsen() -> None:
    from models.t2d.calibration import apply_calibration, expected_calibration_error
    from models.t2d.metrics import brier

    art = _load_artifact()
    y, pr = _test_fold_raw_probs(art)
    cal = apply_calibration(pr, art["calibration"])

    ece_raw = expected_calibration_error(y, pr)
    ece_cal = expected_calibration_error(y, cal)
    # Honest: calibration improves (or does not worsen) probability quality on the test fold.
    assert ece_cal <= ece_raw + 1e-3, (
        f"calibration worsened ECE: raw={ece_raw:.5f} calibrated={ece_cal:.5f}"
    )
    assert np.isfinite(brier(y, pr)) and np.isfinite(brier(y, cal))


# ---------------------------------------------------------------------------
# 6. Attribution stays REAL + leakage-safe on the calibrated champion
# ---------------------------------------------------------------------------


def test_attribution_real_and_leakage_safe_on_calibrated_champion() -> None:
    from models.leakage_check import assert_no_outcome_features
    from models.t2d.synthetic_data import FORBIDDEN_FEATURES, SEQ_NUMERIC
    from vigil.workers.tasks import LSTMScorer

    art = _load_artifact()
    scorer = LSTMScorer(art)
    pid = "22222222-2222-2222-2222-222222222222"
    attrib = scorer.attributions(
        pd.DataFrame([_static_row(pid)]), _heavy_trajectory(pid)
    )[0]

    names = [r["feature"] for r in attrib["reasons"]]
    assert names, (
        "calibrated champion produced no attribution for an observed trajectory"
    )
    assert set(names) <= set(SEQ_NUMERIC), f"reason features not model inputs: {names}"
    assert_no_outcome_features(names)  # must not raise
    assert "miss_probability" not in names
    assert not (set(names) & set(FORBIDDEN_FEATURES))
    for r in attrib["reasons"]:
        assert r["method"] == "lstm_occlusion"


# ---------------------------------------------------------------------------
# 7. END-TO-END: the real calibrated model drives a serious-risk crossing (no override)
# ---------------------------------------------------------------------------


def test_real_calibrated_model_drives_crossing_end_to_end(
    migrated_db: dict[str, str],
) -> None:
    """A fresh participant with heavy disengagement, scored by the REAL calibrated champion
    through score_trial (no override), lands in the HIGH band and records exactly one crossing."""
    from vigil.db.models import Engagement, Participant, Site, Trial
    from vigil.repositories import scoring as scoring_repo
    from vigil.repositories.session import sponsor_bootstrap_session
    from vigil.workers.tasks import score_trial

    ids = migrated_db
    sponsor_a = uuid.UUID(ids["sponsor_a"])
    trial_id = uuid.uuid4()
    pid = uuid.uuid4()
    site_id = uuid.uuid4()

    # past-anchored timestamps so the temporal guard (decision_time = now) passes.
    base = datetime.now(tz=timezone.utc) - timedelta(days=400)

    with sponsor_bootstrap_session(ids["sponsor_a"]) as session:
        session.add(
            Trial(
                id=trial_id,
                sponsor_id=sponsor_a,
                name="calib-crossing-trial",
                n_sites=3,
                planned_duration_days=365,
                phase="PHASE3",
            )
        )
        session.flush()
        session.add(
            Site(id=site_id, sponsor_id=sponsor_a, trial_id=trial_id, name="calib-site")
        )
        session.flush()
        session.add(
            Participant(
                id=pid,
                sponsor_id=sponsor_a,
                trial_id=trial_id,
                site_id=site_id,
                coded_ref="PT-CALIB-HEAVY",
                status="active",
                age_years=65.0,
                hba1c_pct=7.5,
                bmi=28.0,
                sex="Male",
                arm_type="Placebo Comparator",
            )
        )
        session.flush()
        for _, row in _heavy_trajectory(str(pid)).iterrows():
            session.add(
                Engagement(
                    id=uuid.uuid4(),
                    sponsor_id=sponsor_a,
                    participant_id=pid,
                    trial_id=trial_id,
                    site_id=site_id,
                    visit_index=int(row["visit_index"]),
                    visit_timestamp=base + timedelta(days=30 * int(row["visit_index"])),
                    attended=bool(row["attended"]),
                    missed=bool(row["missed"]),
                    cumulative_missed=int(row["cumulative_missed"]),
                    consecutive_missed=int(row["consecutive_missed"]),
                    synthetic=True,
                )
            )
        session.flush()

    result = asyncio.get_event_loop().run_until_complete(
        score_trial(
            {"job_try": 0},
            trial_id=str(trial_id),
            sponsor_id=ids["sponsor_a"],
            model_version=None,
            regime="t2d",
        )
    )
    assert result["model_version"] == _CHAMPION_MV

    with sponsor_bootstrap_session(ids["sponsor_a"]) as session:
        row = scoring_repo.get_score(session, pid, _CHAMPION_MV)
        assert row is not None, "no champion score row after score_trial"
        assert row.risk_score > _SERIOUS_THRESHOLD, (
            f"real calibrated model scored {row.risk_score:.4f} (<= {_SERIOUS_THRESHOLD}); "
            "the HIGH band is not reachable end-to-end"
        )
        assert row.risk_band == "high"
        crossings = scoring_repo.list_crossings_for_participant(session, pid)
        assert len(crossings) == 1, (
            f"expected exactly one serious-risk crossing, got {len(crossings)}"
        )
        assert crossings[0].risk_band == "high"
        assert crossings[0].prior_band in ("none", None)
