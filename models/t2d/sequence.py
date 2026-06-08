"""TASK 3 — the synthetic-cohort LSTM sequence model (torch ``nn.LSTM``).

A per-visit dropout-risk classifier over each participant's trajectory. At decision time t the
model consumes visits with index < t ONLY (a causal LSTM: the hidden state after step t-1 is the
prediction for "drops out at a later visit"). Static baseline covariates (age, sex, hba1c, bmi,
phase, arm_type, n_sites, duration) are concatenated at every step as context. Per-visit
observable features ONLY: attended/missed (bool->float), cumulative_missed, consecutive_missed,
visit_index. NEVER ``miss_probability`` (the latent hazard), ``synthetic``, provenance, or outcome.

Labelling / horizon (stated): for a participant who DROPS at visit d, every decision point
t in [0, d) has label 1 ("a dropout occurs at a later visit") and t == d is the event itself
(not a decision point — the model flags BEFORE it). For a completer, every decision point has
label 0. Right-censoring is explicit: a participant contributes a labelled decision point at t
only while t < (their last observed visit); positions at/after the last observed visit are masked
out (a participant whose observation has ended cannot carry a label it could not have).

Lead-time: visits between the FIRST decision point whose predicted risk crosses the flag
threshold and the actual dropout visit d, measured on TEST droppers.

Determinism: ``torch.manual_seed(MODEL_SEED)`` + numpy seed; scalers fit on TRAIN only; NO
rebalancing; the forbidden-feature assertion fires before the fit.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

import numpy as np
import pandas as pd
import torch
from torch import nn

from models.config import MODEL_SEED
from models.splits import Split
from models.t2d.metrics import classifier_panel, clip01, per_stratum
from models.t2d.synthetic_data import (
    SEQ_NUMERIC,
    STATIC_CATEGORICAL,
    STATIC_NUMERIC,
    STRATA,
    SyntheticT2D,
    assert_no_forbidden_features,
)

#: Risk threshold above which a decision point is a "confident flag" (for lead-time).
FLAG_THRESHOLD: float = 0.5


@dataclass
class SequenceConfig:
    """Hyper-parameters; small + capped for CPU tractability + determinism."""

    hidden_size: int = 32
    num_layers: int = 1
    epochs: int = 3
    batch_size: int = 256
    lr: float = 1e-3
    max_train_participants: int = 20000  # documented subsample of TRAIN only; eval is full
    max_visits: int = 40


@dataclass
class StaticEncoder:
    """One-hot (train-fit) categoricals + train-fit standardized numerics for static context."""

    levels_: dict[str, list[str]] = field(default_factory=dict)
    means_: dict[str, float] = field(default_factory=dict)
    stds_: dict[str, float] = field(default_factory=dict)
    feature_names_: list[str] = field(default_factory=list)

    def fit(self, train: pd.DataFrame) -> StaticEncoder:
        for c in STATIC_CATEGORICAL:
            self.levels_[c] = sorted(train[c].astype("string").fillna("__NA__").unique().tolist())
        for c in STATIC_NUMERIC:
            vals = pd.to_numeric(train[c], errors="coerce").to_numpy(dtype=float)
            self.means_[c] = float(np.nanmean(vals))
            s = float(np.nanstd(vals))
            self.stds_[c] = s if s > 0 else 1.0
        names: list[str] = []
        for c in STATIC_CATEGORICAL:
            names += [f"{c}={lvl}" for lvl in self.levels_[c]]
        names += list(STATIC_NUMERIC)
        self.feature_names_ = names
        return self

    def transform(self, rows: pd.DataFrame) -> np.ndarray:
        blocks: list[np.ndarray] = []
        for c in STATIC_CATEGORICAL:
            col = rows[c].astype("string").fillna("__NA__")
            onehot = np.zeros((len(rows), len(self.levels_[c])), dtype=np.float32)
            for j, lvl in enumerate(self.levels_[c]):
                onehot[:, j] = (col == lvl).to_numpy()
            blocks.append(onehot)
        for c in STATIC_NUMERIC:
            raw = pd.to_numeric(rows[c], errors="coerce").to_numpy(dtype=float)
            raw = np.nan_to_num(raw, nan=self.means_[c])
            blocks.append((((raw - self.means_[c]) / self.stds_[c]).astype(np.float32)).reshape(-1, 1))
        return np.hstack(blocks).astype(np.float32)


class LSTMClassifier(nn.Module):
    """Static-context-conditioned causal LSTM emitting a per-step dropout-risk logit."""

    def __init__(self, seq_dim: int, static_dim: int, cfg: SequenceConfig) -> None:
        super().__init__()
        self.static_proj = nn.Linear(static_dim, cfg.hidden_size)
        self.lstm = nn.LSTM(
            input_size=seq_dim,
            hidden_size=cfg.hidden_size,
            num_layers=cfg.num_layers,
            batch_first=True,
        )
        self.head = nn.Linear(cfg.hidden_size, 1)
        self.num_layers = cfg.num_layers

    def forward(self, seq: torch.Tensor, static: torch.Tensor) -> torch.Tensor:
        # initial hidden state seeded from the static context (per spec option).
        h0 = torch.tanh(self.static_proj(static)).unsqueeze(0).repeat(self.num_layers, 1, 1)
        c0 = torch.zeros_like(h0)
        out, _ = self.lstm(seq, (h0, c0))  # (B, T, H)
        return self.head(out).squeeze(-1)  # (B, T) logits


@dataclass
class SequenceTensors:
    """Padded per-visit tensors for one fold."""

    seq: np.ndarray  # (N, T, seq_dim) float32 — features at each step (the step's observation)
    static: np.ndarray  # (N, static_dim) float32
    label: np.ndarray  # (N, T) float32 — "drops at a later visit" decision-point label
    mask: np.ndarray  # (N, T) float32 — 1 where the decision point is valid (uncensored)
    participant_id: np.ndarray
    nct_id: np.ndarray
    dropped: np.ndarray  # (N,) bool
    dropout_visit: np.ndarray  # (N,) int (-1 if completer)
    last_obs: np.ndarray  # (N,) int — number of observed visits
    strata: dict[str, np.ndarray]


def _seq_feature_frame(eng: pd.DataFrame) -> pd.DataFrame:
    """The per-visit observable feature frame (bools cast to float); NEVER miss_probability."""
    f = pd.DataFrame(index=eng.index)
    f["attended"] = eng["attended"].astype("float32")
    f["missed"] = eng["missed"].astype("float32")
    f["cumulative_missed"] = eng["cumulative_missed"].astype("float32")
    f["consecutive_missed"] = eng["consecutive_missed"].astype("float32")
    f["visit_index"] = eng["visit_index"].astype("float32")
    return f


def build_tensors(
    synth: SyntheticT2D,
    static_enc: StaticEncoder,
    fold_ids: frozenset[str],
    *,
    cfg: SequenceConfig,
    seq_means: np.ndarray,
    seq_stds: np.ndarray,
    rng: np.random.Generator | None = None,
    subsample: int | None = None,
) -> SequenceTensors:
    """Assemble padded tensors for the participants whose ``nct_id`` is in ``fold_ids``."""
    p = synth.participants[synth.participants["nct_id"].isin(fold_ids)].copy()
    if subsample is not None and len(p) > subsample:
        assert rng is not None
        idx = rng.choice(len(p), size=subsample, replace=False)
        p = p.iloc[np.sort(idx)].reset_index(drop=True)
    pids = p["participant_id"].to_numpy()
    pid_set = set(pids.tolist())

    eng = synth.engagement[synth.engagement["participant_id"].isin(pid_set)].copy()
    eng = eng.sort_values(["participant_id", "visit_index"])
    feat = _seq_feature_frame(eng)
    feat_vals = feat.to_numpy(dtype=np.float32)
    eng_pid = eng["participant_id"].to_numpy()

    # group engagement rows by participant
    n = len(p)
    t_max = cfg.max_visits
    seq_dim = feat_vals.shape[1]
    seq = np.zeros((n, t_max, seq_dim), dtype=np.float32)
    last_obs = np.zeros(n, dtype=np.int64)

    # contiguous slices per participant (sorted)
    order = np.argsort(eng_pid, kind="mergesort")
    eng_pid_sorted = eng_pid[order]
    feat_sorted = feat_vals[order]
    boundaries = np.searchsorted(eng_pid_sorted, pids, side="left")
    boundaries_end = np.searchsorted(eng_pid_sorted, pids, side="right")
    for i, pid in enumerate(pids):
        s, e = boundaries[i], boundaries_end[i]
        length = min(e - s, t_max)
        seq[i, :length, :] = feat_sorted[s : s + length]
        last_obs[i] = length

    # standardize sequence numerics with TRAIN stats (broadcast over valid steps only)
    valid_step = (np.arange(t_max)[None, :] < last_obs[:, None]).astype(np.float32)
    seq = (seq - seq_means) / seq_stds
    seq = seq * valid_step[:, :, None]  # zero out padding after standardization

    dropped = p["dropped"].to_numpy()
    dropout_visit = p["dropout_visit_index"].fillna(-1).to_numpy().astype(np.int64)

    # decision-point labels + censoring mask
    label = np.zeros((n, t_max), dtype=np.float32)
    mask = np.zeros((n, t_max), dtype=np.float32)
    for i in range(n):
        lo = int(last_obs[i])
        if lo == 0:
            continue
        if dropped[i]:
            d = int(dropout_visit[i])  # last observed visit == event visit
            # decision points are the visits strictly before d; all carry label 1
            n_dp = min(d, t_max)
            mask[i, :n_dp] = 1.0
            label[i, :n_dp] = 1.0
        else:
            # completer: every observed visit is a valid (negative) decision point
            mask[i, :lo] = 1.0
            # label stays 0

    static = static_enc.transform(p)
    strata = {sname: p[sname].to_numpy() for sname in STRATA}
    return SequenceTensors(
        seq=seq,
        static=static,
        label=label,
        mask=mask,
        participant_id=pids,
        nct_id=p["nct_id"].to_numpy(),
        dropped=dropped,
        dropout_visit=dropout_visit,
        last_obs=last_obs,
        strata=strata,
    )


def _fit_seq_scaler(
    synth: SyntheticT2D, train_ids: frozenset[str], *, cfg: SequenceConfig
) -> tuple[np.ndarray, np.ndarray]:
    """Fit per-visit-feature mean/std on TRAIN participants' observed visits ONLY."""
    train_pids = set(
        synth.participants.loc[
            synth.participants["nct_id"].isin(train_ids), "participant_id"
        ].tolist()
    )
    eng = synth.engagement[synth.engagement["participant_id"].isin(train_pids)]
    feat = _seq_feature_frame(eng).to_numpy(dtype=np.float32)
    means = feat.mean(axis=0)
    stds = feat.std(axis=0)
    stds[stds == 0] = 1.0
    return means.astype(np.float32), stds.astype(np.float32)


def _predict_steps(
    model: LSTMClassifier, tensors: SequenceTensors, *, batch_size: int
) -> np.ndarray:
    """Per-step risk probabilities (N, T)."""
    model.eval()
    probs = np.zeros_like(tensors.label, dtype=np.float32)
    with torch.no_grad():
        for s in range(0, len(tensors.label), batch_size):
            e = s + batch_size
            seq = torch.from_numpy(tensors.seq[s:e])
            static = torch.from_numpy(tensors.static[s:e])
            logits = model(seq, static)
            probs[s:e] = torch.sigmoid(logits).numpy()
    return probs


def _decision_point_eval(
    tensors: SequenceTensors, probs: np.ndarray
) -> tuple[np.ndarray, np.ndarray, dict[str, np.ndarray]]:
    """Flatten valid decision points to (y, prob) + per-stratum repeats for stratified panels."""
    m = tensors.mask.astype(bool)
    y = tensors.label[m].astype(int)
    pr = clip01(probs[m])
    # repeat each participant's stratum over its valid decision points
    counts = tensors.mask.sum(axis=1).astype(int)
    strata = {
        name: np.repeat(vals.astype(str), counts) for name, vals in tensors.strata.items()
    }
    return y, pr, strata


def lead_time_distribution(
    tensors: SequenceTensors, probs: np.ndarray, *, threshold: float = FLAG_THRESHOLD
) -> dict[str, Any]:
    """Visits between the FIRST flag (risk>=threshold) and the dropout visit, for TEST droppers."""
    leads: list[int] = []
    flagged = 0
    droppers = 0
    for i in range(len(tensors.dropped)):
        if not tensors.dropped[i]:
            continue
        droppers += 1
        d = int(tensors.dropout_visit[i])
        n_dp = min(d, tensors.seq.shape[1])
        if n_dp <= 0:
            continue
        risk = probs[i, :n_dp]
        crossed = np.where(risk >= threshold)[0]
        if crossed.size:
            flagged += 1
            first = int(crossed[0])
            leads.append(d - first)  # visits of warning before the event
    arr = np.asarray(leads, dtype=float)
    return {
        "threshold": threshold,
        "n_test_droppers": droppers,
        "n_flagged": flagged,
        "flag_coverage": float(flagged / droppers) if droppers else float("nan"),
        "median_lead_time_visits": float(np.median(arr)) if arr.size else float("nan"),
        "mean_lead_time_visits": float(np.mean(arr)) if arr.size else float("nan"),
        "p25_lead_time_visits": float(np.percentile(arr, 25)) if arr.size else float("nan"),
        "p75_lead_time_visits": float(np.percentile(arr, 75)) if arr.size else float("nan"),
        "_leads": arr,
    }


def train_sequence_model(
    synth: SyntheticT2D,
    split: Split,
    *,
    cfg: SequenceConfig | None = None,
) -> dict[str, Any]:
    """Fit the LSTM (deterministic, train-only scalers, no rebalancing) and evaluate the panel.

    Returns a dict with the fitted ``model``, the ``static_enc``, fold ``tensors``, per-step
    ``probs``, and the metric panel. Artifact writing/plotting is left to the pipeline.
    """
    cfg = cfg or SequenceConfig()
    torch.manual_seed(MODEL_SEED)
    np.random.seed(MODEL_SEED)
    rng = np.random.default_rng(MODEL_SEED)

    # static encoder fit on TRAIN participants only
    train_p = synth.participants[synth.participants["nct_id"].isin(split.train_ids)]
    static_enc = StaticEncoder().fit(train_p)
    assert_no_forbidden_features(static_enc.feature_names_)  # static context guard
    assert_no_forbidden_features(list(SEQ_NUMERIC))  # per-visit feature guard (fail loud)

    seq_means, seq_stds = _fit_seq_scaler(synth, split.train_ids, cfg=cfg)

    train_t = build_tensors(
        synth,
        static_enc,
        split.train_ids,
        cfg=cfg,
        seq_means=seq_means,
        seq_stds=seq_stds,
        rng=rng,
        subsample=cfg.max_train_participants,
    )
    test_t = build_tensors(
        synth, static_enc, split.test_ids, cfg=cfg, seq_means=seq_means, seq_stds=seq_stds
    )
    val_t = build_tensors(
        synth, static_enc, split.val_ids, cfg=cfg, seq_means=seq_means, seq_stds=seq_stds
    )

    seq_dim = train_t.seq.shape[2]
    static_dim = train_t.static.shape[1]
    model = LSTMClassifier(seq_dim, static_dim, cfg)

    optimizer = torch.optim.Adam(model.parameters(), lr=cfg.lr)
    loss_fn = nn.BCEWithLogitsLoss(reduction="none")  # NO pos_weight => NO rebalancing

    seq_t = torch.from_numpy(train_t.seq)
    static_tt = torch.from_numpy(train_t.static)
    label_t = torch.from_numpy(train_t.label)
    mask_t = torch.from_numpy(train_t.mask)

    n = len(train_t.label)
    first_batch_loss: float | None = None
    model.train()
    for _epoch in range(cfg.epochs):
        perm = torch.randperm(n, generator=torch.Generator().manual_seed(MODEL_SEED + _epoch))
        for bi, s in enumerate(range(0, n, cfg.batch_size)):
            idx = perm[s : s + cfg.batch_size]
            optimizer.zero_grad()
            logits = model(seq_t[idx], static_tt[idx])
            raw = loss_fn(logits, label_t[idx])
            m = mask_t[idx]
            loss = (raw * m).sum() / m.sum().clamp_min(1.0)
            loss.backward()
            optimizer.step()
            if _epoch == 0 and bi == 0:
                first_batch_loss = float(loss.detach())

    test_probs = _predict_steps(model, test_t, batch_size=cfg.batch_size)
    val_probs = _predict_steps(model, val_t, batch_size=cfg.batch_size)

    y_test, pr_test, strata_test = _decision_point_eval(test_t, test_probs)
    y_val, pr_val, strata_val = _decision_point_eval(val_t, val_probs)

    lead = lead_time_distribution(test_t, test_probs)

    panel: dict[str, Any] = {
        "task": "3_sequence_lstm_synthetic",
        "seed": MODEL_SEED,
        "config": {
            "hidden_size": cfg.hidden_size,
            "num_layers": cfg.num_layers,
            "epochs": cfg.epochs,
            "batch_size": cfg.batch_size,
            "lr": cfg.lr,
            "max_train_participants": cfg.max_train_participants,
        },
        "n_train_participants": int(len(train_t.label)),
        "n_test_participants": int(len(test_t.label)),
        "n_test_decision_points": int(len(y_test)),
        "first_batch_loss": first_batch_loss,
        "test": {
            "overall": classifier_panel(y_test, pr_test),
            "per_stratum": per_stratum(strata_test, y_test, pr_test),
        },
        "val": {"overall": classifier_panel(y_val, pr_val)},
        "lead_time": {k: v for k, v in lead.items() if not k.startswith("_")},
    }
    return {
        "panel": panel,
        "model": model,
        "static_enc": static_enc,
        "test_tensors": test_t,
        "test_probs": test_probs,
        "test_y": y_test,
        "test_pr": pr_test,
        "lead": lead,
        "seq_means": seq_means,
        "seq_stds": seq_stds,
        "cfg": cfg,
    }
