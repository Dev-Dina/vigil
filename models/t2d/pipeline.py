"""The T2D pipeline driver: 1a -> 1b -> pre-register (BEFORE fit) -> sequence -> attribution -> card.

Ordering is load-bearing: the pre-registration file is written from 1b's actual PR-AUC *before*
any LSTM weight is touched; the evaluated result is appended only afterwards. Every artifact lands
under ``data/models/t2d/`` (git-ignored); the pan-indication ``data/models/`` artifacts are never
touched.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import matplotlib

matplotlib.use("Agg")

import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

from models.cards.template import render_model_card  # noqa: E402
from models.config import MODELS_ROOT  # noqa: E402
from models.t2d.attribution import global_feature_gradients, local_occlusion  # noqa: E402
from models.t2d.baseline_real import train_real_baseline  # noqa: E402
from models.t2d.baseline_synthetic import train_synthetic_structural  # noqa: E402
from models.t2d.cohort import T2DCohort, load_t2d_cohort  # noqa: E402
from models.t2d.preregistration import write_preregistration  # noqa: E402
from models.t2d.sequence import SequenceConfig, train_sequence_model  # noqa: E402
from models.t2d.synthetic_data import SyntheticT2D, load_synthetic_t2d  # noqa: E402
from models.plots import plot_calibration_curve, plot_pr_curve  # noqa: E402

T2D_ROOT = MODELS_ROOT / "t2d"

HELD_OUT_AXIS = (
    "temporal, trial-level, keyed on ref_trial.start_date, group-disjoint by nct_id "
    "(earlier-starting trials -> train, later -> test). Within-participant visit-time is the "
    "sequence the LSTM consumes; lead-time is measured in visits before the dropout event."
)


def _plot_lead_time(leads: np.ndarray, out: Path) -> Path:
    fig, ax = plt.subplots(figsize=(6.0, 4.0), dpi=120)
    if leads.size:
        bins = np.arange(0, int(leads.max()) + 2) - 0.5
        ax.hist(leads, bins=bins, color="#2f5f8f", edgecolor="#1f3f5f")
        ax.axvline(
            float(np.median(leads)), color="#c0392b", linestyle="--", label="median"
        )
        ax.legend()
    ax.set(
        title="Lead-time distribution (visits before dropout)",
        xlabel="lead-time (visits)",
        ylabel="count",
    )
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, format="png", bbox_inches="tight")
    plt.close(fig)
    return out


def _plot_per_stratum(
    per_stratum: dict[str, dict[str, dict[str, float]]], metric: str, out: Path
) -> Path:
    fig, axes = plt.subplots(
        1, len(per_stratum), figsize=(5.0 * len(per_stratum), 4.0), dpi=120
    )
    if len(per_stratum) == 1:
        axes = [axes]
    for ax, (sname, levels) in zip(axes, per_stratum.items(), strict=False):
        names = list(levels.keys())
        vals = [levels[n].get(metric, float("nan")) for n in names]
        ax.bar(names, vals, color="#6f7f8f", edgecolor="#4a5560")
        ax.set(title=f"{metric} by {sname}", ylabel=metric)
        ax.tick_params(axis="x", rotation=30)
    fig.tight_layout()
    out.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out, format="png", bbox_inches="tight")
    plt.close(fig)
    return out


def _bar_result(
    seq_pr_auc: float, prereg: dict[str, Any], median_lead: float
) -> dict[str, Any]:
    pr_ok = bool(seq_pr_auc >= prereg["criteria"]["pr_auc"]["must_reach"])
    lead_ok = bool(median_lead >= prereg["criteria"]["lead_time"]["must_reach"])
    return {
        "sequence_test_pr_auc": round(float(seq_pr_auc), 4),
        "must_reach_pr_auc": prereg["criteria"]["pr_auc"]["must_reach"],
        "pr_auc_pass": pr_ok,
        "sequence_median_lead_time_visits": median_lead,
        "must_reach_lead_time": prereg["criteria"]["lead_time"]["must_reach"],
        "lead_time_pass": lead_ok,
        "overall_pass": bool(pr_ok and lead_ok),
    }


def run_t2d_pipeline(
    cohort: T2DCohort,
    synth: SyntheticT2D,
    *,
    out_root: Path = T2D_ROOT,
    seq_cfg: SequenceConfig | None = None,
) -> dict[str, Any]:
    """Run the full T2D layer end-to-end and write all artifacts. Returns a result summary."""
    out_root = Path(out_root)
    out_root.mkdir(parents=True, exist_ok=True)
    split = cohort.split

    # --- TASK 1a: real floor ---------------------------------------------------------------
    real = train_real_baseline(cohort, out_root=out_root)

    # --- TASK 1b: synthetic structural-only ablation (the bar) -----------------------------
    synth_struct = train_synthetic_structural(synth, split, out_root=out_root)
    bar_pr_auc = synth_struct["test"]["gbt"]["pr_auc"]

    # --- TASK 2: pre-register BEFORE the sequence fit --------------------------------------
    prereg = write_preregistration(bar_pr_auc, out_root=out_root)

    # --- TASK 3: sequence model (fit happens AFTER prereg is on disk) ----------------------
    seq = train_sequence_model(synth, split, cfg=seq_cfg)
    panel = seq["panel"]
    seq_pr_auc = panel["test"]["overall"]["pr_auc"]
    median_lead = panel["lead_time"]["median_lead_time_visits"]

    # Persist the LSTM artifact so the scoring worker can load it.
    # Artifact name: sequence_v1.0:demo → sequence_v1.0_demo.pt (colon→underscore).
    # The version string is documented here; workers/tasks.py must match _DEMO_MODEL_VERSION.
    _persist_sequence_artifact(seq, seq_pr_auc, out_root)

    # sequence plots
    seq_plots: dict[str, str] = {}
    seq_plots["pr_sequence"] = str(
        plot_pr_curve(seq["test_y"], seq["test_pr"], out_root / "pr_sequence.png")
    )
    seq_plots["calibration_sequence"] = str(
        plot_calibration_curve(
            seq["test_y"], seq["test_pr"], out_root / "calib_sequence.png"
        )
    )
    seq_plots["lead_time"] = str(
        _plot_lead_time(seq["lead"]["_leads"], out_root / "lead_time.png")
    )
    seq_plots["per_stratum_pr_auc"] = str(
        _plot_per_stratum(
            panel["test"]["per_stratum"], "pr_auc", out_root / "per_stratum_pr_auc.png"
        )
    )

    # attribution
    test_t = seq["test_tensors"]
    g_path, g_imp = global_feature_gradients(
        seq["model"], test_t, out_root / "attr_global.png"
    )
    seq_plots["attr_global"] = str(g_path)
    # local: a few high-risk TEST droppers
    dropper_pos = np.where(test_t.dropped)[0]
    final_risk = np.array(
        [
            seq["test_probs"][i, max(int(test_t.mask[i].sum()) - 1, 0)]
            for i in dropper_pos
        ]
    )
    top = dropper_pos[np.argsort(-final_risk)[:3]].tolist() if dropper_pos.size else []
    local_paths = local_occlusion(seq["model"], test_t, top, out_root) if top else []
    seq_plots["attr_local"] = [str(p) for p in local_paths]

    # --- bar verdict + append result to prereg --------------------------------------------
    bar = _bar_result(seq_pr_auc, prereg, median_lead)
    prereg_with_result = {**prereg, "evaluated_result": bar}
    (out_root / "preregistration.json").write_text(
        json.dumps(prereg_with_result, indent=2), encoding="utf-8"
    )

    # --- sequence_metrics.json -------------------------------------------------------------
    seq_metrics = {
        **panel,
        "held_out_axis": HELD_OUT_AXIS,
        "bar_vs_1b": bar,
        "global_attribution_top": g_imp,
        "artifacts": seq_plots,
    }
    (out_root / "sequence_metrics.json").write_text(
        json.dumps(seq_metrics, indent=2), encoding="utf-8"
    )

    # --- model card ------------------------------------------------------------------------
    _write_card(cohort, real, synth_struct, prereg, panel, bar, seq_plots, out_root)

    return {
        "out_root": str(out_root),
        "held_out_axis": HELD_OUT_AXIS,
        "real_1a": real,
        "synthetic_1b": synth_struct,
        "preregistration": prereg,
        "sequence_panel": panel,
        "bar": bar,
        "lead_time": panel["lead_time"],
    }


def _write_card(
    cohort: T2DCohort,
    real: dict[str, Any],
    synth_struct: dict[str, Any],
    prereg: dict[str, Any],
    panel: dict[str, Any],
    bar: dict[str, Any],
    plots: dict[str, Any],
    out_root: Path,
) -> None:
    real_gbt = real["test"]["gbt"]
    sb = synth_struct["test"]["gbt"]
    seq = panel["test"]["overall"]
    meta = {
        "model_name": "Vigil Phase-3 T2D Sequence Model (synthetic-cohort LSTM) + structural anchors",
        "data_source": (
            "SYNTHETIC T2D cohort — per-participant sequences GENERATED and calibrated to real "
            "T2D ClinicalTrials.gov/AACT aggregate statistics plus labelled literature priors. "
            "NOT real participants. NO PHI. Method-validity / partner-readiness only, NEVER a "
            "clinical prediction. The 1a anchor is the REAL T2D registry floor (aggregate per-arm)."
        ),
        "model_type": (
            "torch nn.LSTM per-visit dropout-risk classifier (static covariates seed the initial "
            "hidden state) on the synthetic cohort; structural anchors are "
            "HistGradientBoosting + LogisticRegression on real (1a) and synthetic-structural (1b)."
        ),
        "cohort_description": (
            f"755 T2D INDUSTRY modelling-cohort trials / {len(cohort.arms)} guarded real arms "
            f"(started>=20, dropout_rate<1.0); {panel['n_train_participants']} synthetic train "
            f"participants (documented subsample), full test fold. Held-out axis: {HELD_OUT_AXIS} "
            f"Train {cohort.split.train_dates[0]}..{cohort.split.train_dates[1]}, "
            f"val {cohort.split.val_dates[0]}..{cohort.split.val_dates[1]}, "
            f"test {cohort.split.test_dates[0]}..{cohort.split.test_dates[1]}."
        ),
        "target": (
            "per-visit decision-time dropout label: at t the model sees visits<t and predicts "
            "whether the participant drops at a LATER visit; right-censored decision points are "
            "masked. 1a target = continuous per-arm dropout_rate; 1b = terminal participant "
            "dropped (censored==observed completer==negative, no early right-censoring)."
        ),
        "features": [
            "sequence (per visit, observable ONLY): attended, missed, cumulative_missed, "
            "consecutive_missed, visit_index",
            "static context: age_years, sex, hba1c_pct, bmi, phase, arm_type, n_sites, "
            "planned_duration_days",
            "EXCLUDED as features (fail-loud): miss_probability (LATENT generator hazard), "
            "synthetic, *_baseline_imputed provenance, dropped/censored/arm_real_dropout_rate/"
            "dropout_visit_index/dropout_reason (outcome), ids",
        ],
        "metrics": {
            "1a_real_floor_gbt_pr_auc": real_gbt["pr_auc"],
            "1a_real_floor_gbt_mae": real_gbt["mae"],
            "1a_real_floor_logit_pr_auc": real["test"]["logit"]["pr_auc"],
            "1b_synthetic_structural_pr_auc": sb["pr_auc"],
            "1b_synthetic_structural_recall_at_p50": sb["recall_at_p50"],
            "1b_synthetic_structural_brier": sb["brier"],
            "preregistered_bar_pr_auc_must_reach": prereg["criteria"]["pr_auc"][
                "must_reach"
            ],
            "preregistered_bar_median_lead_time": prereg["criteria"]["lead_time"][
                "must_reach"
            ],
            "sequence_pr_auc": seq["pr_auc"],
            "sequence_recall_at_p50": seq["recall_at_p50"],
            "sequence_brier": seq["brier"],
            "sequence_median_lead_time_visits": panel["lead_time"][
                "median_lead_time_visits"
            ],
            "bar_pr_auc_PASS": bar["pr_auc_pass"],
            "bar_lead_time_PASS": bar["lead_time_pass"],
            "bar_overall_PASS": bar["overall_pass"],
            "per_sponsor_class": {
                # the T2D strata are (arm_type, phase); the card template expects a per-class
                # mapping, so we surface the sequence per-stratum panels here for the table.
                **{
                    f"arm_type:{k}": v
                    for k, v in panel["test"]["per_stratum"]["arm_type"].items()
                },
                **{
                    f"phase:{k}": v
                    for k, v in panel["test"]["per_stratum"]["phase"].items()
                },
            },
        },
        "calibration_note": (
            f"Sequence Brier (test, decision points) = {seq['brier']:.4f}; 1b structural Brier = "
            f"{sb['brier']:.4f}; 1a real GBT Brier = {real_gbt['brier']:.4f}. See calib_*.png. "
            "The planted trajectory->dropout relationship is noisy + non-separable (AUC~0.77 by "
            "construction), so neither model is expected to be near-perfect."
        ),
        "limitations": [
            "SYNTHETIC cohort: generated per-participant sequences calibrated to real T2D AACT "
            "aggregates + literature priors — NOT real participants, NO PHI, method-validity only.",
            "The trajectory->dropout relationship is a PLANTED, literature-shaped assumption "
            "(>=3 consecutive missed visits / hazard threshold, noisy). The sequence model "
            "beating 1b demonstrates the ARCHITECTURE and the incremental value of trajectory "
            "features ON THIS COHORT — it does NOT validate the dropout-precursor hypothesis on "
            "real data.",
            "BMI is ~80% literature-prior (imputed); HbA1c ~55% imputed; imputed covariates carry "
            "NO planted signal (provenance flags are never features).",
            "miss_probability (the generator's LATENT hazard) is NEVER a feature — using it would "
            "trivially recover the planted rule and destroy the honesty guard.",
            "Sponsor-level leakage is an ACCEPTED limitation: the split is group-disjoint by "
            "nct_id, not by sponsor (sponsor identity is never a feature).",
            "No early right-censoring exists in this cohort (censored==observed completer); the "
            "per-visit mask still enforces decision-time censoring for the sequence labels.",
            "No rebalancing anywhere (per the Evaluation contract); scalers/encoders fit on TRAIN "
            "only; the forbidden-feature assertion fires before every fit.",
        ],
    }
    card = render_model_card(meta)
    (out_root / "model_card.md").write_text(card, encoding="utf-8")


def _persist_sequence_artifact(
    seq: dict[str, Any],
    seq_pr_auc: float,
    out_root: Path,
) -> Path:
    """Save the trained LSTM to disk and verify the reload reproduces the PR-AUC.

    Artifact dict schema (consumed by vigil/workers/tasks.py LSTMScorer):
      state_dict, cfg, static_enc, seq_means, seq_stds, seq_dim, static_dim, test_pr_auc
    """
    import torch

    from models.t2d.metrics import classifier_panel
    from models.t2d.sequence import LSTMClassifier, _decision_point_eval, _predict_steps

    artifact_path = out_root / "sequence_v1.0_demo.pt"
    model: LSTMClassifier = seq["model"]
    test_t = seq["test_tensors"]

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
    torch.save(artifact, artifact_path)

    # Verify reload: reconstruct model and re-score test fold.
    reloaded = torch.load(str(artifact_path), weights_only=False)
    model_ck = LSTMClassifier(
        reloaded["seq_dim"], reloaded["static_dim"], reloaded["cfg"]
    )
    model_ck.load_state_dict(reloaded["state_dict"])
    ck_probs = _predict_steps(model_ck, test_t, batch_size=reloaded["cfg"].batch_size)
    ck_y, ck_pr, _ = _decision_point_eval(test_t, ck_probs)
    ck_auc = classifier_panel(ck_y, ck_pr)["pr_auc"]
    if abs(ck_auc - seq_pr_auc) > 0.001:
        raise ValueError(
            f"Reloaded model PR-AUC {ck_auc:.4f} deviates from trained {seq_pr_auc:.4f} "
            "(state_dict or scaler mismatch — artifact not trustworthy)"
        )
    return artifact_path


def run_from_disk(
    *, out_root: Path = T2D_ROOT, seq_cfg: SequenceConfig | None = None
) -> dict[str, Any]:
    """Load real clean/raw + synthetic from disk and run the full pipeline."""
    cohort = load_t2d_cohort()
    synth = load_synthetic_t2d()
    return run_t2d_pipeline(cohort, synth, out_root=out_root, seq_cfg=seq_cfg)
