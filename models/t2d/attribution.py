"""Temporal attribution for the LSTM (gradient + occlusion over per-visit features).

A torch model with a per-step output makes ``shap.DeepExplainer`` fragile for sequences, so we
use two transparent, deterministic attributions and save PNGs:

- **Global**: mean |gradient| of the final-decision-point risk w.r.t. each per-visit input
  feature (averaged over a sample of TEST participants) -> a horizontal bar of feature influence.
- **Occlusion (local)**: for a few high-risk TEST droppers, zero each visit step in turn and
  record the change in the pre-event risk -> a per-visit influence curve showing WHICH visits
  drive the flag (the trajectory story the planted rule encodes).
"""

from __future__ import annotations

from pathlib import Path

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402
import torch  # noqa: E402

from models.t2d.sequence import LSTMClassifier, SequenceTensors  # noqa: E402
from models.t2d.synthetic_data import SEQ_NUMERIC  # noqa: E402


def _decision_index(tensors: SequenceTensors, i: int) -> int:
    """The last valid decision point for participant i (the pre-event / final risk read-out)."""
    valid = int(tensors.mask[i].sum())
    return max(valid - 1, 0)


def global_feature_gradients(
    model: LSTMClassifier,
    tensors: SequenceTensors,
    out: Path,
    *,
    sample: int = 200,
) -> tuple[Path, dict[str, float]]:
    """Mean |d risk / d feature| at each participant's final decision point -> bar PNG."""
    n = min(sample, len(tensors.label))
    seq = torch.from_numpy(tensors.seq[:n]).clone().requires_grad_(True)
    static = torch.from_numpy(tensors.static[:n])
    model.eval()
    logits = model(seq, static)  # (n, T)
    risk = torch.sigmoid(logits)
    # sum the final-decision-point risk across the sample, backprop once
    picks = torch.tensor([_decision_index(tensors, i) for i in range(n)])
    selected = risk[torch.arange(n), picks].sum()
    selected.backward()
    grads = seq.grad.detach().abs().numpy()  # (n, T, seq_dim)
    # mean over participants + valid steps
    valid = (np.arange(tensors.seq.shape[1])[None, :] < tensors.last_obs[:n, None]).astype(float)
    weight = valid[:, :, None]
    mean_abs = (grads * weight).sum(axis=(0, 1)) / np.clip(weight.sum(axis=(0, 1)), 1.0, None)
    importance = dict(
        sorted(zip(SEQ_NUMERIC, mean_abs.tolist(), strict=True), key=lambda kv: -kv[1])
    )
    labels = list(importance.keys())[::-1]
    heights = [importance[k] for k in labels]
    fig, ax = plt.subplots(figsize=(7.0, 4.0), dpi=120)
    ax.barh(labels, heights, color="#2f5f8f", edgecolor="#1f3f5f")
    ax.set(title="LSTM temporal attribution (mean |gradient|)", xlabel="mean |d risk / d feature|")
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, format="png", bbox_inches="tight")
    plt.close(fig)
    return out, importance


def local_occlusion(
    model: LSTMClassifier,
    tensors: SequenceTensors,
    example_idx: list[int],
    out_dir: Path,
    *,
    prefix: str = "attr_local",
) -> list[Path]:
    """Per-visit occlusion influence on the pre-event risk for a few example participants."""
    out_dir.mkdir(parents=True, exist_ok=True)
    model.eval()
    paths: list[Path] = []
    for k, i in enumerate(example_idx):
        lo = int(tensors.last_obs[i])
        dp = _decision_index(tensors, i)
        base_seq = torch.from_numpy(tensors.seq[i : i + 1]).clone()
        static = torch.from_numpy(tensors.static[i : i + 1])
        with torch.no_grad():
            base_risk = float(torch.sigmoid(model(base_seq, static))[0, dp])
            influence = []
            for t in range(lo):
                occ = base_seq.clone()
                occ[0, t, :] = 0.0
                r = float(torch.sigmoid(model(occ, static))[0, dp])
                influence.append(base_risk - r)
        fig, ax = plt.subplots(figsize=(7.0, 3.5), dpi=120)
        ax.bar(range(lo), influence, color="#6f7f8f", edgecolor="#4a5560")
        ax.axhline(0.0, color="#888888", linewidth=1.0)
        ax.set(
            title=f"Per-visit occlusion influence (example {k}, base risk={base_risk:.2f})",
            xlabel="visit index",
            ylabel="drop in risk when visit occluded",
        )
        fig.tight_layout()
        out = out_dir / f"{prefix}_{k}.png"
        fig.savefig(out, format="png", bbox_inches="tight")
        plt.close(fig)
        paths.append(out)
    return paths
