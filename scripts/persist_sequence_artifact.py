"""Generate and persist the sequence LSTM artifact (sequence_v1.0_demo.pt).

Trains the sequence model on the synthetic T2D cohort without running the
full sklearn-dependent pipeline. Saves to data/models/t2d/sequence_v1.0_demo.pt
and verifies the reload reproduces the trained PR-AUC.

Run:
    uv run python scripts/persist_sequence_artifact.py
"""

from __future__ import annotations

import json
from pathlib import Path

import torch

from models.t2d.cohort import load_t2d_cohort
from models.t2d.metrics import classifier_panel
from models.t2d.sequence import (
    LSTMClassifier,
    _decision_point_eval,
    _predict_steps,
    train_sequence_model,
)
from models.t2d.synthetic_data import load_synthetic_t2d

_ARTIFACT_PATH = Path("data/models/t2d/sequence_v1.0_demo.pt")
_TRAINED_PR_AUC = 0.3390  # pre-registered bar PASSED


def main() -> None:
    print("Loading cohort split...")
    cohort = load_t2d_cohort()
    split = cohort.split

    print("Loading synthetic T2D data...")
    synth = load_synthetic_t2d()

    print("Training sequence model (deterministic, MODEL_SEED)...")
    seq = train_sequence_model(synth, split)
    seq_pr_auc: float = seq["panel"]["test"]["overall"]["pr_auc"]
    print(f"  test PR-AUC: {seq_pr_auc:.4f}  (trained bar {_TRAINED_PR_AUC})")

    model: LSTMClassifier = seq["model"]
    test_t = seq["test_tensors"]

    _ARTIFACT_PATH.parent.mkdir(parents=True, exist_ok=True)
    artifact = {
        "state_dict": model.state_dict(),
        "cfg": seq["cfg"],
        "static_enc": seq["static_enc"],
        "seq_means": seq["seq_means"],
        "seq_stds": seq["seq_stds"],
        "seq_dim": int(test_t.seq.shape[2]),
        "static_dim": int(test_t.static.shape[1]),
        "test_pr_auc": float(seq_pr_auc),
    }
    torch.save(artifact, _ARTIFACT_PATH)
    print(f"  saved: {_ARTIFACT_PATH}")

    # Verify reload reproduces PR-AUC within tolerance.
    reloaded = torch.load(str(_ARTIFACT_PATH), weights_only=False)
    model_ck = LSTMClassifier(
        reloaded["seq_dim"], reloaded["static_dim"], reloaded["cfg"]
    )
    model_ck.load_state_dict(reloaded["state_dict"])
    ck_probs = _predict_steps(model_ck, test_t, batch_size=reloaded["cfg"].batch_size)
    ck_y, ck_pr, _ = _decision_point_eval(test_t, ck_probs)
    ck_auc = classifier_panel(ck_y, ck_pr)["pr_auc"]
    delta = abs(ck_auc - seq_pr_auc)
    if delta > 0.001:
        raise ValueError(
            f"Reload PR-AUC {ck_auc:.4f} deviates {delta:.4f} from trained "
            f"{seq_pr_auc:.4f} (tolerance 0.001) — artifact not trustworthy"
        )
    print(f"  reload verify: PR-AUC={ck_auc:.4f}  delta={delta:.6f}  OK")

    result = {
        "artifact": str(_ARTIFACT_PATH),
        "test_pr_auc": round(seq_pr_auc, 4),
        "reload_pr_auc": round(ck_auc, 4),
        "delta": round(delta, 6),
        "status": "ok",
    }
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
