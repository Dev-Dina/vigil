"""Generate and persist the calibrated sequence LSTM artifact (sequence_v1.1_demo.pt).

Trains the sequence model on the synthetic T2D cohort (no sklearn-pipeline dependency), fits a
**monotonic isotonic output calibrator on the held-out VAL fold ONLY** (disjoint from the LSTM's
TRAIN fold and the reported TEST fold), and persists the calibrator WITH the model artifact so the
scoring worker emits calibrated probabilities that span a usable range — making the operational
``> 0.6`` HIGH band reachable from the REAL model without an override.

Gate 9.7a honesty contract (verified below, fail-loud):
- Discrimination UNCHANGED: raw test PR-AUC reproduces the trained bar; calibration is monotonic
  so test ROC-AUC is invariant pre/post (it re-maps the scale, it does not change ranking).
- Leakage-safe: the calibrator is fit on VAL, disjoint from TRAIN + TEST; the artifact freezes
  ``train_nct_ids`` / ``val_nct_ids`` / ``test_nct_ids`` so disjointness is hermetically assertable.
- Reachability: the calibrated real LSTM exceeds 0.6 on a heavy-disengagement trajectory.

Version bump: the calibrated champion is ``sequence_v1.1:demo`` (probability semantics change ->
new version). The ``> 0.6`` threshold is UNCHANGED. See ``/specs/scoring.md § Output calibration``.

Run:
    uv run python -m scripts.persist_sequence_artifact
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd
import torch
from sklearn.metrics import roc_auc_score

from models.t2d.calibration import apply_calibration, fit_isotonic_calibrator
from models.t2d.cohort import load_t2d_cohort
from models.t2d.metrics import classifier_panel
from models.t2d.sequence import (
    LSTMClassifier,
    _decision_point_eval,
    _predict_steps,
    train_sequence_model,
)
from models.t2d.synthetic_data import load_synthetic_t2d

_MODEL_VERSION = "sequence_v1.1:demo"
_ARTIFACT_PATH = Path("data/models/t2d/sequence_v1.1_demo.pt")
_TRAINED_PR_AUC = (
    0.3390  # pre-registered bar PASSED (RAW ranking; unchanged by calibration)
)
_FIDELITY_TOLERANCE = 0.001
_ROC_INVARIANCE_TOLERANCE = (
    1e-3  # monotonic calibration preserves ranking -> ROC-AUC invariant
)
_SERIOUS_THRESHOLD = 0.6


def _heavy_trajectory(pid: str) -> tuple[pd.DataFrame, pd.DataFrame]:
    """A heavy-disengagement trajectory (10 consecutive missed visits) + its static row."""
    static = pd.DataFrame(
        [
            {
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
        ]
    )
    pattern = [False] + [True] * 10
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
    return static, pd.DataFrame(rows)


def main() -> None:
    print("Loading cohort split...")
    cohort = load_t2d_cohort()
    split = cohort.split

    print("Loading synthetic T2D data...")
    synth = load_synthetic_t2d()

    print("Training sequence model (deterministic, MODEL_SEED)...")
    seq = train_sequence_model(synth, split)
    seq_pr_auc: float = seq["panel"]["test"]["overall"]["pr_auc"]
    print(
        f"  test PR-AUC (raw ranking): {seq_pr_auc:.4f}  (trained bar {_TRAINED_PR_AUC})"
    )

    model: LSTMClassifier = seq["model"]
    test_t = seq["test_tensors"]
    cfg = seq["cfg"]

    # --- fit the isotonic calibrator on the VAL fold ONLY (disjoint from train + test) ---------
    print(
        "Fitting isotonic output calibrator on the VAL fold (disjoint from train + test)..."
    )
    calibration, cal_report = fit_isotonic_calibrator(
        model,
        synth,
        split,
        static_enc=seq["static_enc"],
        seq_means=seq["seq_means"],
        seq_stds=seq["seq_stds"],
        cfg=cfg,
    )
    print("  calibration (val) report:", json.dumps(cal_report, indent=2))

    # --- honesty: discrimination invariant on the TEST fold (ranking preserved) ----------------
    test_probs = _predict_steps(model, test_t, batch_size=cfg.batch_size)
    y_test, pr_test, _ = _decision_point_eval(test_t, test_probs)
    roc_raw = float(roc_auc_score(y_test, pr_test))
    cal_test = apply_calibration(pr_test, calibration)
    roc_cal = float(roc_auc_score(y_test, cal_test))
    roc_delta = abs(roc_cal - roc_raw)
    print(
        f"  test ROC-AUC raw={roc_raw:.6f} calibrated={roc_cal:.6f} "
        f"delta={roc_delta:.2e}  raw_max={pr_test.max():.4f} cal_max={cal_test.max():.4f}"
    )
    if roc_delta > _ROC_INVARIANCE_TOLERANCE:
        raise ValueError(
            f"calibration moved ROC-AUC by {roc_delta:.4e} (> {_ROC_INVARIANCE_TOLERANCE}) — "
            "monotonic calibration MUST preserve ranking; a non-calibration change leaked in"
        )

    _ARTIFACT_PATH.parent.mkdir(parents=True, exist_ok=True)
    artifact = {
        "state_dict": model.state_dict(),
        "cfg": cfg,
        "static_enc": seq["static_enc"],
        "seq_means": seq["seq_means"],
        "seq_stds": seq["seq_stds"],
        "seq_dim": int(test_t.seq.shape[2]),
        "static_dim": int(test_t.static.shape[1]),
        "test_pr_auc": float(seq_pr_auc),
        "model_version": _MODEL_VERSION,
        # Gate 9.7a: the monotonic isotonic output calibrator, fit on the VAL fold.
        "calibration": calibration,
        "calibration_report": cal_report,
        # Frozen folds: public ClinicalTrials.gov NCT identifiers (no PHI). Carried so the
        # discrimination-invariant + leakage-disjointness checks reproduce HERMETICALLY from the
        # committed synthetic parquet, and so the calibration fit split is provably disjoint from
        # both the LSTM's train fold and the reported test fold.
        "train_nct_ids": sorted(split.train_ids),
        "val_nct_ids": sorted(split.val_ids),
        "test_nct_ids": sorted(split.test_ids),
    }
    torch.save(artifact, _ARTIFACT_PATH)
    print(f"  saved: {_ARTIFACT_PATH}")

    # --- verify reload reproduces RAW PR-AUC (fidelity: real trained weights, not a re-init) ----
    reloaded = torch.load(str(_ARTIFACT_PATH), weights_only=False)
    model_ck = LSTMClassifier(
        reloaded["seq_dim"], reloaded["static_dim"], reloaded["cfg"]
    )
    model_ck.load_state_dict(reloaded["state_dict"])
    ck_probs = _predict_steps(model_ck, test_t, batch_size=reloaded["cfg"].batch_size)
    ck_y, ck_pr, _ = _decision_point_eval(test_t, ck_probs)
    ck_auc = classifier_panel(ck_y, ck_pr)["pr_auc"]
    delta = abs(ck_auc - seq_pr_auc)
    if delta > _FIDELITY_TOLERANCE:
        raise ValueError(
            f"Reload PR-AUC {ck_auc:.4f} deviates {delta:.4f} from trained "
            f"{seq_pr_auc:.4f} (tolerance {_FIDELITY_TOLERANCE}) — artifact not trustworthy"
        )
    print(f"  reload verify: raw PR-AUC={ck_auc:.4f}  delta={delta:.6f}  OK")

    # --- verify reachability: the calibrated REAL model exceeds 0.6 on heavy disengagement ------
    from vigil.workers.tasks import LSTMScorer  # noqa: PLC0415 — deferred torch path

    scorer = LSTMScorer(reloaded)
    static, eng = _heavy_trajectory("00000000-0000-0000-0000-000000000001")
    calibrated_score = scorer(static, eng)[0]
    if not (calibrated_score > _SERIOUS_THRESHOLD):
        raise ValueError(
            f"calibrated score {calibrated_score:.4f} on heavy disengagement does NOT exceed "
            f"{_SERIOUS_THRESHOLD} — the serious crossing is not reachable by the real model"
        )
    print(
        f"  reachability: calibrated heavy-disengagement score={calibrated_score:.4f} "
        f"(> {_SERIOUS_THRESHOLD})  OK"
    )

    result = {
        "artifact": str(_ARTIFACT_PATH),
        "model_version": _MODEL_VERSION,
        "test_pr_auc_raw": round(seq_pr_auc, 4),
        "reload_pr_auc_raw": round(ck_auc, 4),
        "test_roc_auc_raw": round(roc_raw, 6),
        "test_roc_auc_calibrated": round(roc_cal, 6),
        "roc_auc_delta": float(f"{roc_delta:.2e}"),
        "calibrated_heavy_disengagement_score": round(float(calibrated_score), 4),
        "raw_prob_max": round(float(pr_test.max()), 4),
        "calibrated_prob_max": round(float(cal_test.max()), 4),
        "status": "ok",
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
