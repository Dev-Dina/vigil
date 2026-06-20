"""Task functions. Jobs carry the scope context they need explicitly.

score_trial's writeback APPENDS a timestamped history row (no unique key; see
specs/scoring.md § Writeback), so it is NOT upsert-idempotent: a retry APPENDS a new point
rather than overwriting. Retry stays safe for correctness (each row is independent,
RLS-scoped, audited) but may leave a duplicate near-coincident history point — cosmetic, never
a cross-tenant or non-champion leak.

score_trial runs the full scoring pipeline:
  resolve scope -> load cohort + engagement -> temporal guard -> feature guards
  -> inference (LSTM or demo) -> compute risk_band -> writeback (append + audit)
  -> denorm update on participant
Every path fires assert_feature_time_before_t (when engagement exists) +
assert_no_outcome_features before inference; these cannot be skipped.
Invariant 8: synthetic=True is stamped on the score when ANY engagement row is synthetic.
Invariant 9: LSTMScorer calls models.t2d.sequence._seq_feature_frame (training-time path).
"""

from __future__ import annotations

import asyncio
import random
import uuid
from pathlib import Path
from typing import Any

from vigil.core.config import get_settings
from vigil.core.logging import get_logger

log = get_logger("vigil.worker")

# Sentinel model version used in demo mode when no real artifact exists.
# Gate 9.7a: the champion is the isotonic-CALIBRATED sequence model (sequence_v1.1:demo).
_DEMO_MODEL_VERSION = "sequence_v1.1:demo"

# Default model card reference path.
_DEFAULT_MODEL_CARD = "data/models/t2d/model_card.md"


# ---------------------------------------------------------------------------
# LSTM scorer — wraps a loaded .pt artifact for per-participant scoring.
# All torch imports are deferred to __init__ / __call__ so importing this
# module never loads torch (light-suite isolation invariant).
# ---------------------------------------------------------------------------


class LSTMScorer:
    """Wraps a loaded sequence_v1.1_demo.pt artifact for live inference.

    __init__ and __call__ import torch lazily so the module-level import of
    vigil.workers.tasks does NOT load torch (light-suite isolation).
    Invariant 9: feature assembly uses models.t2d.sequence._seq_feature_frame,
    the same function used at training time — no parallel scoring-only builder.

    Gate 9.7a: if the artifact carries a ``calibration`` map (the monotonic isotonic output
    calibrator fit on the held-out VAL fold), ``__call__`` applies it so emitted scores span a
    usable probability range and ``> 0.6`` is reachable from the REAL model. The calibrator is a
    monotone post-hoc re-map of the probability SCALE only — it preserves ranking (discrimination
    unchanged). Attribution is computed on the model's OWN (pre-calibration) output (see
    ``attributions``). An artifact with no ``calibration`` key scores exactly as before (identity).
    """

    def __init__(self, artifact: dict) -> None:
        import torch  # deferred — do NOT move to module level

        import models.t2d.sequence as _seq_mod  # noqa: PLC0415 — intentional lazy import

        model = _seq_mod.LSTMClassifier(
            artifact["seq_dim"], artifact["static_dim"], artifact["cfg"]
        )
        model.load_state_dict(artifact["state_dict"])
        model.eval()
        self._model = model
        self._static_enc = artifact["static_enc"]
        self._seq_means = artifact["seq_means"]
        self._seq_stds = artifact["seq_stds"]
        self._cfg = artifact["cfg"]
        # Gate 9.7a: monotonic output calibrator (None for a legacy uncalibrated artifact).
        self._calibration = artifact.get("calibration")
        # Suppress unused-import warning; torch is needed for no-grad context.
        self._torch = torch

    def _assemble(self, participant_df: Any, eng_df: Any) -> tuple[Any, Any, Any]:
        """Build the EXACT (seq, static_input, last_obs) tensors the model scores.

        Shared by ``__call__`` and ``attributions`` so attribution perturbs the very inputs the
        model scored (single feature path; invariant 9 — no parallel builder).
        seq is standardized (N, T, seq_dim); a channel's standardized mean is 0.0, the neutral
        baseline occlusion uses.
        """
        import numpy as np  # deferred

        import models.t2d.sequence as _seq_mod  # noqa: PLC0415

        t_max = self._cfg.max_visits
        seq_dim = int(self._seq_means.shape[0])

        n = len(participant_df)
        seq = np.zeros((n, t_max, seq_dim), dtype=np.float32)
        last_obs = np.zeros(n, dtype=np.int64)

        pids = participant_df["participant_id"].tolist()
        eng_has_rows = (
            not eng_df.empty if hasattr(eng_df, "empty") else bool(len(eng_df))
        )

        for i, pid in enumerate(pids):
            if not eng_has_rows:
                continue
            p_eng = eng_df[eng_df["participant_id"] == pid].sort_values("visit_index")
            if len(p_eng) == 0:
                continue
            # Invariant 9: call training-time _seq_feature_frame, NOT a parallel builder.
            feat = _seq_mod._seq_feature_frame(p_eng).to_numpy(dtype=np.float32)
            n_v = min(len(feat), t_max)
            feat_std = (feat[:n_v] - self._seq_means) / self._seq_stds
            seq[i, :n_v, :] = feat_std
            last_obs[i] = n_v

        static_input = self._static_enc.transform(
            participant_df.reset_index(drop=True)
        ).astype(np.float32)
        return seq, static_input, last_obs

    def _final_probs(self, seq: Any, static_input: Any, last_obs: Any) -> Any:
        """Per-participant final-OBSERVED-step risk probability (np.ndarray, float64)."""
        import numpy as np  # deferred

        torch = self._torch
        with torch.no_grad():
            logits = self._model(
                torch.from_numpy(seq),
                torch.from_numpy(static_input),
            )  # (N, T) logits
            probs = torch.sigmoid(logits).numpy()  # (N, T)
        n = seq.shape[0]
        out = np.empty(n, dtype=np.float64)
        for i in range(n):
            n_v = int(last_obs[i])
            out[i] = float(probs[i, n_v - 1]) if n_v > 0 else 0.5
        return out

    def _calibrate(self, probs: Any) -> Any:
        """Apply the monotonic isotonic output calibrator (identity if the artifact has none)."""
        from models.t2d.calibration import apply_calibration  # noqa: PLC0415 — numpy-only

        return apply_calibration(probs, self._calibration)

    def __call__(self, participant_df: Any, eng_df: Any) -> list[float]:
        """Score each participant from their engagement trajectory.

        participant_df: one row per participant with all 8 static features.
          Columns: participant_id, age_years, hba1c_pct, bmi, sex, arm_type,
                   n_sites, planned_duration_days, phase.
        eng_df: engagement rows for all participants; must have participant_id col.
        Returns: list[float] — final-step CALIBRATED risk probability per participant (Gate 9.7a:
          the monotonic isotonic calibrator re-maps the scale so ``> 0.6`` is reachable; ranking,
          hence discrimination, is unchanged).
        """
        seq, static_input, last_obs = self._assemble(participant_df, eng_df)
        return [
            float(v)
            for v in self._calibrate(self._final_probs(seq, static_input, last_obs))
        ]

    def attributions(
        self, participant_df: Any, eng_df: Any, *, top_k: int = 3
    ) -> list[dict[str, Any]]:
        """REAL per-participant occlusion attribution over the LSTM's OWN sequence inputs.

        Method (leakage-safe, model-grounded): re-score with each sequence channel neutralised
        (set to 0 in standardized space = its train mean) and measure the change in THIS
        participant's final-step risk. The signed delta ``base - occluded`` is that channel's
        contribution to the model's score — a genuine attribution of the model's OWN computation,
        perturbing ONLY the model's input channels (``SEQ_NUMERIC``; never an outcome/latent/
        provenance field — those are not in the sequence at all). On synthetic data the planted
        signal (consecutive missed visits) surfaces here because it is what the model used.
        Participants with no observed trajectory get NO reasons (honest: nothing to attribute).

        Gate 9.7a: the deltas are computed on the model's OWN (pre-calibration) output — the
        quantity the LSTM actually produces — NOT the calibrated probability. This keeps the
        deltas meaningful (a monotone isotonic flat region never zeroes them) and the calibrator,
        being monotone, does not reorder feature importances; attribution is invariant to it.

        Returns one dict per participant (aligned to ``participant_df`` row order):
        ``{"top_factors": [name,...], "reasons": [{"feature","contribution","method"},...]}``.
        """
        import numpy as np  # deferred

        from models.t2d.synthetic_data import SEQ_NUMERIC  # noqa: PLC0415

        seq, static_input, last_obs = self._assemble(participant_df, eng_df)
        base = self._final_probs(seq, static_input, last_obs)
        seq_dim = seq.shape[2]
        names = list(SEQ_NUMERIC)[:seq_dim]

        # One occlusion forward pass per channel, batched over all participants.
        deltas = np.zeros((seq.shape[0], seq_dim), dtype=np.float64)
        for c in range(seq_dim):
            occ = seq.copy()
            occ[:, :, c] = 0.0  # standardized mean = neutral baseline for channel c
            deltas[:, c] = base - self._final_probs(occ, static_input, last_obs)

        out: list[dict[str, Any]] = []
        for i in range(seq.shape[0]):
            if int(last_obs[i]) == 0:
                out.append({"top_factors": [], "reasons": []})
                continue
            order = np.argsort(-np.abs(deltas[i]))[:top_k]
            reasons = [
                {
                    "feature": names[c],
                    "contribution": round(float(deltas[i, c]), 6),
                    "method": "lstm_occlusion",
                }
                for c in order
                if abs(deltas[i, c]) > 0.0
            ]
            out.append(
                {"top_factors": [r["feature"] for r in reasons], "reasons": reasons}
            )
        return out


# ---------------------------------------------------------------------------
# Structural GBT scorer — wraps the loaded structural_v1.0_t2d.pkl artifact.
# Sklearn-based; no torch. Used as the shadow model alongside the LSTM champion.
# ---------------------------------------------------------------------------


class StructuralScorer:
    """Wraps a loaded structural_v1.0_t2d.pkl artifact for live shadow inference.

    Converts per-participant records to arm-like rows via the training-time
    ContractTransformer, then runs HistGradientBoostingRegressor.predict().
    Only arm_type, phase, n_sites, planned_duration_days are populated from
    the operational DB; all other arm-contract features are NaN (GBT-safe).
    """

    def __init__(self, artifact: dict) -> None:
        self._gbt = artifact["gbt"]
        self._transformer = artifact["transformer"]
        self._threshold = artifact["threshold"]

    def feature_names_for(self, participant_df: Any) -> list[str]:
        """Return the feature names produced by the ContractTransformer for leakage checks."""
        rows = self._build_arm_rows(participant_df)
        _, names = self._transformer.transform(rows)
        return names

    def _build_arm_rows(self, participant_df: Any) -> Any:
        """Map participant records to arm-like rows with available fields; rest NaN."""
        import numpy as np
        import pandas as pd

        from models.features.contract import (
            BOOLEAN_FEATURES,
            CATEGORICAL_FEATURES,
            NUMERIC_FEATURES,
        )

        n = len(participant_df)
        data: dict[str, list] = {col: [None] * n for col in CATEGORICAL_FEATURES}
        data.update({col: [np.nan] * n for col in NUMERIC_FEATURES})
        data.update({col: [None] * n for col in BOOLEAN_FEATURES})

        for i, (_, p) in enumerate(participant_df.iterrows()):
            if "phase" in participant_df.columns:
                data["phase"][i] = p.get("phase")
            if "arm_type" in participant_df.columns:
                data["arm_type"][i] = p.get("arm_type")
            if "n_sites" in participant_df.columns and pd.notna(p.get("n_sites")):
                data["n_sites"][i] = float(p["n_sites"])
            if "planned_duration_days" in participant_df.columns and pd.notna(
                p.get("planned_duration_days")
            ):
                data["planned_duration_days"][i] = float(p["planned_duration_days"])

        return pd.DataFrame(data)

    def __call__(self, participant_df: Any) -> list[float]:
        """Score participants; returns dropout-risk probabilities clipped to [0, 1]."""
        rows = self._build_arm_rows(participant_df)
        X, _ = self._transformer.transform(rows)
        raw_preds = self._gbt.predict(X)
        return [float(max(0.0, min(1.0, v))) for v in raw_preds]

    def attributions(
        self, participant_df: Any, *, top_k: int = 3
    ) -> list[dict[str, Any]]:
        """REAL per-participant occlusion attribution over the structural model's OWN features.

        Method (leakage-safe, model-grounded): re-predict with each ContractTransformer feature
        set to NaN (HistGradientBoosting's native missing handling = neutral) and measure the
        change in THIS participant's predicted risk. The signed delta ``base - occluded`` is that
        feature's contribution to the model's score — its OWN attribution over ONLY its input
        features (identical surface to scoring; no outcome/leaky column is ever a feature). Only
        the features actually populated for a participant move the score; the rest delta to 0 and
        are dropped (honest — no fabricated contribution). Shadow attributions are stored, never
        surfaced to users (champion-only read surface, B4).
        """
        import numpy as np  # deferred

        rows = self._build_arm_rows(participant_df)
        X, names = self._transformer.transform(rows)
        # Keep X as the transformer returned it (carrying feature names) so re-predict matches the
        # scoring call exactly — converting to a bare array would trip sklearn's name-mismatch path.
        base = np.array(
            [max(0.0, min(1.0, v)) for v in self._gbt.predict(X)], dtype=np.float64
        )
        n_rows = X.shape[0]
        n_feat = X.shape[1]
        deltas = np.zeros((n_rows, n_feat), dtype=np.float64)
        for j in range(n_feat):
            occ = X.copy()
            occ.iloc[:, j] = (
                np.nan
            )  # HGBR native missing = neutral baseline (names preserved)
            occ_pred = np.array(
                [max(0.0, min(1.0, v)) for v in self._gbt.predict(occ)],
                dtype=np.float64,
            )
            deltas[:, j] = base - occ_pred

        out: list[dict[str, Any]] = []
        for i in range(n_rows):
            order = np.argsort(-np.abs(deltas[i]))[:top_k]
            reasons = [
                {
                    "feature": names[c],
                    "contribution": round(float(deltas[i, c]), 6),
                    "method": "structural_occlusion",
                }
                for c in order
                if abs(deltas[i, c]) > 0.0
            ]
            out.append(
                {"top_factors": [r["feature"] for r in reasons], "reasons": reasons}
            )
        return out


# ---------------------------------------------------------------------------
# Demo / stub scorer (random scores, fixed seed — no-artifact fallback only)
# ---------------------------------------------------------------------------


def _demo_scorer(features: Any) -> list[float]:  # noqa: ANN401
    """Returns deterministic random scores — method demo only, no real model."""
    rng = random.Random(42)
    n = len(features) if hasattr(features, "__len__") else 1
    return [rng.random() for _ in range(n)]


def _resolve_champion_version(regime: str) -> str:
    """Query routing_state for the champion model version; hard error if absent.

    specs/routing.md § Resolver contract: no silent sentinel fallback.
    """
    from vigil.repositories import routing as routing_repo
    from vigil.repositories.session import platform_session

    with platform_session() as session:
        row = routing_repo.get_champion(session, regime=regime)
    if row is None:
        raise ValueError(
            f"No champion model registered for regime {regime!r}. "
            "Seed routing_state before scoring. "
            f"(Demo: regime='t2d', model_version='{_DEMO_MODEL_VERSION}')"
        )
    return row.model_version


def _load_scorer(
    model_version: str | None,
    ctx: dict[str, Any],
    regime: str | None = None,
) -> tuple[Any, str]:
    """Return (scorer_callable, resolved_model_version).

    Priority:
    1. ctx['_scorer_override'] — test injection; skips artifact + registry logic.
    2. Explicit model_version provided → use it directly.
    3. regime provided, model_version=None → query routing_state for champion.
    4. Neither provided in demo_mode → regime='t2d' with a loud warning.
    5. Neither provided in production → hard error (routing.md § Resolver contract).

    For .pt artifacts, returns an LSTMScorer (real inference).
    _demo_scorer (rng) is only returned as explicit no-artifact fallback in demo_mode.
    """
    if "_scorer_override" in ctx:
        mv = model_version or _DEMO_MODEL_VERSION
        return ctx["_scorer_override"], mv

    settings = get_settings()

    if model_version is not None:
        mv = model_version
    elif regime is not None:
        mv = _resolve_champion_version(regime)
    elif settings.demo_mode:
        log.warning(
            "score_trial.regime_unset",
            extra={
                "extra": {
                    "reason": "regime not provided; defaulting to 't2d' in demo mode",
                    "action": "wire regime from ScoringTriggerIn in B2/B3",
                }
            },
        )
        mv = _resolve_champion_version("t2d")
    else:
        raise ValueError(
            "score_trial: regime must be provided when model_version is None "
            "(specs/routing.md § Resolver contract). "
            "Pass regime=<indication_code> to resolve the champion model."
        )

    # Try .pt (torch LSTM artifact) first, then .pkl (legacy).
    for ext in (".pt", ".pkl"):
        artifact_path = Path("data/models/t2d") / f"{mv.replace(':', '_')}{ext}"
        if not artifact_path.exists():
            continue
        if ext == ".pt":
            import torch  # deferred — torch isolation invariant

            raw = torch.load(str(artifact_path), weights_only=False)
            return LSTMScorer(raw), mv
        else:
            import pickle

            with artifact_path.open("rb") as fh:
                scorer = pickle.load(fh)  # noqa: S301 — controlled path
            return scorer, mv

    # No artifact found.
    if settings.demo_mode:
        log.warning(
            "score_trial.demo_scorer",
            extra={"extra": {"model_version": mv, "reason": "no artifact; demo mode"}},
        )
        return _demo_scorer, mv

    raise FileNotFoundError(
        f"No model artifact found for version {mv!r} at data/models/t2d/. "
        "Set VIGIL_DEMO_MODE=true to use the demo scorer (method demo only)."
    )


def _load_shadow_scorer(regime: str) -> tuple[Any, str] | None:
    """Return (StructuralScorer, model_version) for the registered shadow, or None.

    Shadow is always loaded from its persisted .pkl artifact; there is no override
    path — only the real artifact is admitted (specs/routing.md § Champion/challenger/shadow).
    Returns None if no shadow row is registered or artifact is missing (non-fatal: champion
    scoring continues; log warning so ops can act).
    """
    from vigil.repositories import routing as routing_repo
    from vigil.repositories.session import platform_session

    with platform_session() as session:
        row = routing_repo.get_shadow(session, regime=regime)
    if row is None:
        return None

    mv = row.model_version
    artifact_path = Path("data/models/t2d") / f"{mv.replace(':', '_')}.pkl"
    if not artifact_path.exists():
        log.warning(
            "score_trial.shadow_artifact_missing",
            extra={"extra": {"model_version": mv, "path": str(artifact_path)}},
        )
        return None

    import joblib  # deferred; sklearn/joblib not loaded at module level

    raw = joblib.load(artifact_path)
    return StructuralScorer(raw), mv


# ---------------------------------------------------------------------------
# score_trial Arq task
# ---------------------------------------------------------------------------


async def score_trial(
    ctx: dict[str, Any],
    trial_id: str,
    model_version: str | None = None,
    sponsor_id: str | None = None,
    regime: str | None = None,
) -> dict[str, Any]:
    """Score all participants in a trial and write back risk scores.

    Job sequence (exact per specs/scoring.md):
    1. Jitter (backoff spread across retries; writeback appends, so retry is NOT idempotent)
    2. Resolve scorer + model version (fail loud if no artifact in prod)
    3. Load participants + trial metadata + all engagement for the trial
    4. assert_feature_time_before_t (temporal guard; fires when engagement exists)
    5. assert_no_outcome_features on sequence feature names (constant guard)
    6. EXCLUDED_FROM_FEATURES check (same features)
    7. Score (LSTMScorer or _demo_scorer)
    8. Compute risk_band thresholds (>0.6 high, >0.3 medium, else low)
    9. Writeback: append_score + write_score_audit + denorm Participant
       synthetic flag = logical-OR over participant's engagement rows (inv 8)
    10. Return summary dict (no PII, no risk values in log)
    """
    # 1. Jitter — exponential backoff spread across retries (NOT idempotency: the writeback
    #    appends, so a retry adds a new history point — see module docstring).
    job_try = ctx.get("job_try", 0)
    await asyncio.sleep(random.uniform(0, 2**job_try))  # noqa: S311

    log.info(
        "score_trial.start",
        extra={"extra": {"trial_id": trial_id, "model_version": model_version}},
    )

    # 2. Load scorer.
    scorer, resolved_mv = _load_scorer(model_version, ctx, regime=regime)

    # 3. Load participants + trial metadata + engagement.
    import pandas as pd
    from sqlalchemy import select

    from vigil.db.models import Engagement, Participant, Trial
    from vigil.repositories import scoring as scoring_repo
    from vigil.repositories.session import sponsor_bootstrap_session

    if sponsor_id is None:
        raise ValueError(
            "score_trial: sponsor_id must be passed in kwargs (set by enqueue)"
        )

    participant_snapshots: list[dict[str, Any]] = []
    eng_snapshots: list[dict[str, Any]] = []

    with sponsor_bootstrap_session(sponsor_id) as session:
        trial_obj = session.get(Trial, uuid.UUID(trial_id))
        trial_meta: dict[str, Any] = {}
        if trial_obj is not None:
            trial_meta = {
                "n_sites": trial_obj.n_sites,
                "planned_duration_days": trial_obj.planned_duration_days,
                "phase": trial_obj.phase,
            }

        participants = list(
            session.execute(
                select(Participant).where(Participant.trial_id == uuid.UUID(trial_id))
            ).scalars()
        )
        participant_snapshots = [
            {
                "id": p.id,
                "sponsor_id": p.sponsor_id,
                "trial_id": p.trial_id,
                "site_id": p.site_id,
                "coded_ref": p.coded_ref,
                "age_years": p.age_years,
                "hba1c_pct": p.hba1c_pct,
                "bmi": p.bmi,
                "sex": p.sex,
                "arm_type": getattr(p, "arm_type", None),
                **trial_meta,
            }
            for p in participants
        ]

        eng_all = list(
            session.execute(
                select(Engagement).where(Engagement.trial_id == uuid.UUID(trial_id))
            ).scalars()
        )
        eng_snapshots = [
            {
                "participant_id": e.participant_id,
                "visit_index": e.visit_index,
                "visit_timestamp": e.visit_timestamp,
                "attended": e.attended,
                "missed": e.missed,
                "cumulative_missed": e.cumulative_missed,
                "consecutive_missed": e.consecutive_missed,
                "synthetic": e.synthetic,
            }
            for e in eng_all
        ]

    if not participant_snapshots:
        log.info(
            "score_trial.no_participants",
            extra={"extra": {"trial_id": trial_id, "sponsor_id": sponsor_id}},
        )
        return {
            "trial_id": trial_id,
            "n_scored": 0,
            "model_version": resolved_mv,
            "synthetic": True,
        }

    # 4. Temporal guard: all engagement feature timestamps must precede decision_time.
    from datetime import datetime, timezone as _tz

    from models.leakage_check import (
        assert_feature_time_before_t,
        assert_no_outcome_features,
    )

    decision_time = datetime.now(tz=_tz.utc)

    if eng_snapshots:
        eng_guard_df = pd.DataFrame(eng_snapshots)
        eng_guard_df["decision_time"] = decision_time
        assert_feature_time_before_t(
            eng_guard_df, t_col="decision_time", feature_time_col="visit_timestamp"
        )

    # 5–6. Sequence feature name guards (constant; run once regardless of scorer).
    from models.t2d.synthetic_data import SEQ_NUMERIC

    seq_feature_names = list(SEQ_NUMERIC)
    assert_no_outcome_features(seq_feature_names)

    from ingestion.errors import LeakageError
    from models.features.contract import EXCLUDED_FROM_FEATURES

    excluded_present = [c for c in seq_feature_names if c in EXCLUDED_FROM_FEATURES]
    if excluded_present:
        raise LeakageError(
            f"excluded columns present in sequence scoring features: {excluded_present}"
        )

    # 7. Score.
    if isinstance(scorer, LSTMScorer):
        # Real LSTM path (B2b): build participant + engagement DataFrames.
        participant_df = pd.DataFrame(
            [
                {
                    "participant_id": str(p["id"]),
                    "age_years": p.get("age_years"),
                    "hba1c_pct": p.get("hba1c_pct"),
                    "bmi": p.get("bmi"),
                    "sex": p.get("sex"),
                    "arm_type": p.get("arm_type"),
                    "n_sites": p.get("n_sites"),
                    "planned_duration_days": p.get("planned_duration_days"),
                    "phase": p.get("phase"),
                }
                for p in participant_snapshots
            ]
        )

        if eng_snapshots:
            eng_df = pd.DataFrame(eng_snapshots)
            eng_df["participant_id"] = eng_df["participant_id"].astype(str)
        else:
            eng_df = pd.DataFrame(
                columns=[
                    "participant_id",
                    "visit_index",
                    "visit_timestamp",
                    "attended",
                    "missed",
                    "cumulative_missed",
                    "consecutive_missed",
                    "synthetic",
                ]
            )

        scores = scorer(participant_df, eng_df)

        # Gate 9.1: REAL model-attribution reasons (occlusion over the LSTM's OWN inputs).
        champ_attrib = scorer.attributions(participant_df, eng_df)

        # Invariant 8: synthetic = logical-OR over engagement rows per participant.
        pid_synth: dict[str, bool] = {}
        for e in eng_snapshots:
            pid_str = str(e["participant_id"])
            pid_synth[pid_str] = pid_synth.get(pid_str, False) or bool(e["synthetic"])
        synth_flags = [
            pid_synth.get(str(p["id"]), True)  # no engagement → demo default True
            for p in participant_snapshots
        ]

    else:
        # Demo / _scorer_override path: trivial feature representation (backward compat).
        feature_df = pd.DataFrame(
            [
                {
                    "participant_idx": i,
                    "coded_ref_hash": hash(p["coded_ref"]) % 1_000_000,
                }
                for i, p in enumerate(participant_snapshots)
            ]
        )
        feature_names = list(feature_df.columns)
        assert_no_outcome_features(feature_names)
        excluded = [c for c in feature_names if c in EXCLUDED_FROM_FEATURES]
        if excluded:
            raise LeakageError(
                f"outcome/identity columns present in scoring features: {excluded}"
            )
        scores = scorer(feature_df)
        synth_flags = [True] * len(participant_snapshots)
        # Demo/override scorers (e.g. _demo_scorer = rng) support NO genuine attribution — write
        # NO reasons rather than fabricate one (specs/scoring.md § Phase 9 honesty rule).
        champ_attrib = [
            {"top_factors": [], "reasons": []} for _ in participant_snapshots
        ]

    if len(scores) != len(participant_snapshots):
        raise ValueError(
            f"scorer returned {len(scores)} scores for "
            f"{len(participant_snapshots)} participants"
        )

    # 8. Compute risk_band.
    def _band(score: float) -> str:
        if score > 0.6:
            return "high"
        if score > 0.3:
            return "medium"
        return "low"

    # 9.1 leakage guard (sacred): attribution perturbs ONLY the model's input features — assert the
    # reason feature names carry NO outcome/leaky token, the same guard the feature path runs. The
    # attribution introduces no new feature surface; this makes that explicit + fail-loud.
    _attrib_names = sorted({r["feature"] for a in champ_attrib for r in a["reasons"]})
    if _attrib_names:
        assert_no_outcome_features(_attrib_names)

    # 9.2 prior champion band per participant (BEFORE any append this run) — the transition
    # reference for serious-risk crossing detection. Champion allowlist from the platform table.
    from vigil.repositories import routing as routing_repo
    from vigil.repositories.session import platform_session

    with platform_session() as _psession:
        champion_versions = routing_repo.champion_model_versions(_psession)

    # 9. Writeback.
    n_scored = 0
    crossings_to_notify: list[
        str
    ] = []  # 9.6: enqueue a PII-free doorbell per NEW crossing
    with sponsor_bootstrap_session(sponsor_id) as session:
        from sqlalchemy import update

        # Newest champion row per participant that exists BEFORE this run's appends — its band is
        # the "prior champion band" the crossing transition is measured against.
        prior_champ = scoring_repo.champion_scores_by_participant(
            session,
            [p["id"] for p in participant_snapshots],
            champion_versions=champion_versions,
        )
        prior_band = {pid: row.risk_band for pid, row in prior_champ.items()}

        for p_snap, score, synth_flag, attrib in zip(
            participant_snapshots, scores, synth_flags, champ_attrib, strict=True
        ):
            band = _band(float(score))
            # REAL model attributions (Gate 9.1); the synthetic flag travels WITH each reason.
            reasons = [{**r, "synthetic": bool(synth_flag)} for r in attrib["reasons"]]
            score_row = scoring_repo.append_score(
                session,
                participant_id=p_snap["id"],
                sponsor_id=p_snap["sponsor_id"],
                trial_id=p_snap["trial_id"],
                site_id=p_snap["site_id"],
                risk_score=float(score),
                risk_band=band,
                top_factors=list(attrib["top_factors"]),
                reasons=reasons,
                model_version=resolved_mv,
                model_card_ref=_DEFAULT_MODEL_CARD,
                synthetic=bool(synth_flag),
            )
            scoring_repo.write_score_audit(
                session,
                sponsor_id=p_snap["sponsor_id"],
                participant_id=p_snap["id"],
                model_version=resolved_mv,
                synthetic=bool(synth_flag),
                n_rows=1,
            )
            session.execute(
                update(Participant)
                .where(Participant.id == p_snap["id"])
                .values(risk_score=float(score), risk_band=band)
            )
            # 9.2 serious-risk crossing: a non-high -> high champion transition. Idempotent (the
            # crossing is anchored on this score row's id; a retry's prior band is already high so
            # no new transition fires; staying high fires nothing). NO email here — that is 9.6.
            pre = prior_band.get(p_snap["id"])  # None when no prior champion point
            if band == "high" and pre != "high":
                crossing = scoring_repo.record_crossing(
                    session, score_row=score_row, prior_band=pre
                )
                if crossing is not None:
                    crossings_to_notify.append(str(crossing.id))
            n_scored += 1

    # 9.6 enqueue the PII-free doorbell for each NEW crossing — AFTER the writeback commits (so the
    # job can load the row), as an Arq job (never inline; like scoring). The send-once guard is the
    # crossing's `notified` flag, so a best-effort enqueue is safe. A failed enqueue must NOT break
    # scoring: the crossing is recorded (notified=false) and can be re-driven later.
    if crossings_to_notify:
        from vigil.workers.settings import enqueue

        for cid in crossings_to_notify:
            try:
                await enqueue(
                    "send_crossing_notification",
                    crossing_id=cid,
                    sponsor_id=sponsor_id,
                )
            except Exception:  # noqa: BLE001 - notification enqueue is best-effort, never break scoring
                log.warning(
                    "notify.enqueue_failed", extra={"extra": {"crossing_id": cid}}
                )

    # 10. Shadow scoring (B2c): structural GBT runs alongside champion if registered.
    #     Shadow writes participant_score rows ONLY — never touches participant denorm cache.
    #     Invariant (ii): assert_no_outcome_features fires on shadow feature names.
    if regime is not None and participant_snapshots:
        shadow_result = _load_shadow_scorer(regime)
        if shadow_result is not None:
            shadow_scorer_inst, shadow_mv = shadow_result
            shadow_participant_df = pd.DataFrame(
                [
                    {
                        "participant_id": str(p["id"]),
                        "age_years": p.get("age_years"),
                        "hba1c_pct": p.get("hba1c_pct"),
                        "bmi": p.get("bmi"),
                        "sex": p.get("sex"),
                        "arm_type": p.get("arm_type"),
                        "n_sites": p.get("n_sites"),
                        "planned_duration_days": p.get("planned_duration_days"),
                        "phase": p.get("phase"),
                    }
                    for p in participant_snapshots
                ]
            )

            # Invariant (ii): leakage check fires for shadow, identical to champion path.
            shadow_feature_names = shadow_scorer_inst.feature_names_for(
                shadow_participant_df
            )
            assert_no_outcome_features(shadow_feature_names)
            shadow_excluded = [
                c for c in shadow_feature_names if c in EXCLUDED_FROM_FEATURES
            ]
            if shadow_excluded:
                raise LeakageError(
                    f"excluded columns in shadow feature matrix: {shadow_excluded}"
                )

            shadow_scores = shadow_scorer_inst(shadow_participant_df)
            # REAL shadow attributions (occlusion over the structural model's OWN features);
            # STORED for analysis, NEVER surfaced to users (champion-only read surface, B4).
            shadow_attrib = shadow_scorer_inst.attributions(shadow_participant_df)

            # Shadow synthetic flag: same logical-OR over engagement rows as champion (inv 8).
            shadow_pid_synth: dict[str, bool] = {}
            for e in eng_snapshots:
                pid_str = str(e["participant_id"])
                shadow_pid_synth[pid_str] = shadow_pid_synth.get(
                    pid_str, False
                ) or bool(e["synthetic"])

            with sponsor_bootstrap_session(sponsor_id) as session:
                for p_snap, sh_score, sh_attrib in zip(
                    participant_snapshots, shadow_scores, shadow_attrib, strict=True
                ):
                    sh_synth = shadow_pid_synth.get(str(p_snap["id"]), True)
                    sh_reasons = [
                        {**r, "synthetic": bool(sh_synth)} for r in sh_attrib["reasons"]
                    ]
                    scoring_repo.append_score(
                        session,
                        participant_id=p_snap["id"],
                        sponsor_id=p_snap["sponsor_id"],
                        trial_id=p_snap["trial_id"],
                        site_id=p_snap["site_id"],
                        risk_score=float(sh_score),
                        risk_band=_band(float(sh_score)),
                        top_factors=list(sh_attrib["top_factors"]),
                        reasons=sh_reasons,
                        model_version=shadow_mv,
                        model_card_ref="data/models/t2d/model_card_structural.md",
                        synthetic=bool(sh_synth),
                    )
                    scoring_repo.write_score_audit(
                        session,
                        sponsor_id=p_snap["sponsor_id"],
                        participant_id=p_snap["id"],
                        model_version=shadow_mv,
                        synthetic=bool(sh_synth),
                    )
                    # Explicitly NO participant denorm update — shadow never writes
                    # participant.risk_score / participant.risk_band (champion-only guard).

            log.info(
                "score_trial.shadow_complete",
                extra={
                    "extra": {
                        "trial_id": trial_id,
                        "shadow_mv": shadow_mv,
                        "n_shadow_scored": len(shadow_scores),
                    }
                },
            )

    log.info(
        "score_trial.complete",
        extra={
            "extra": {
                "trial_id": trial_id,
                "n_scored": n_scored,
                "model_version": resolved_mv,
            }
        },
    )
    return {
        "trial_id": trial_id,
        "n_scored": n_scored,
        "model_version": resolved_mv,
        "synthetic": True,
    }


# ---------------------------------------------------------------------------
# ping — trivial job proving the async path end-to-end
# ---------------------------------------------------------------------------


async def ping(ctx: dict[str, Any], note: str = "pong") -> dict[str, str]:
    """Trivial job proving the async path end-to-end (enqueue -> worker -> result)."""
    log.info("worker.ping", extra={"extra": {"note": note}})
    return {"status": "ok", "note": note}


# ---------------------------------------------------------------------------
# send_crossing_notification — Gate 9.6 PII-free serious-risk doorbell (SMTP)
# ---------------------------------------------------------------------------


async def send_crossing_notification(
    ctx: dict[str, Any], *, crossing_id: str, sponsor_id: str
) -> dict[str, object]:
    """Send the PII-free doorbell for ONE crossing to its scope-bound recipients, exactly once.

    Thin Arq wrapper over ``notification_service.notify_crossing`` (resolve recipients (9.5) →
    PII-free body → send → flip ``notified``). A send FAILURE is logged and RE-RAISED so Arq retries
    (bounded by ``WorkerSettings.max_tries``); ``notified`` is not flipped on failure, so a later
    try can still deliver. Never inline in the scoring path.
    """
    from vigil.services import notification_service

    try:
        result = notification_service.notify_crossing(
            crossing_id=crossing_id, sponsor_id=sponsor_id
        )
    except Exception:  # noqa: BLE001 - log then re-raise so Arq retries (notified stays false)
        log.warning(
            "notify.send_failed",
            extra={
                "extra": {"crossing_id": crossing_id, "job_try": ctx.get("job_try")}
            },
        )
        raise
    log.info(
        "notify.job_done",
        extra={"extra": {"crossing_id": crossing_id, **result}},
    )
    return result


# ---------------------------------------------------------------------------
# compute_drift — Gate M1 drift producer (PSI/KS over the champion score distribution)
# ---------------------------------------------------------------------------


async def compute_drift(
    ctx: dict[str, Any],
    *,
    regime: str = "t2d",
    demo_shift: float = 0.0,
) -> dict[str, Any]:
    """Compute + persist PSI/KS drift points for the champion score distribution.

    Schedulable (WorkerSettings.cron_jobs) AND manually triggerable (POST /monitoring/drift/run
    enqueues this). Thin wrapper over ``drift_service.run_drift_detection`` — that function does the
    cross-tenant pooling (per-sponsor reads), the reference/current split, the PSI/KS evaluation,
    and the platform-table persistence. ``demo_shift != 0`` constructs a labelled breach demo (NOT
    observed drift). Returns the run summary (no PII; aggregate stats only).
    """
    from dataclasses import asdict

    from vigil.services import drift_service

    summary = drift_service.run_drift_detection(regime=regime, demo_shift=demo_shift)

    # Gate M2: a NEW breach EVENT (the not-breached → breached edge) → enqueue ONE PII-free alert to
    # the ML/platform engineer (never inline; like the crossing doorbell). The send-once guard is the
    # anchor point's ``notified`` flag, so a best-effort enqueue is safe; a re-run over the SAME
    # ongoing breach returns no alert_point_id (the producer edge already deduped). A failed enqueue
    # must NOT break drift computation — the points are persisted and the run can be re-driven.
    if summary.alert_point_id:
        from vigil.workers.settings import enqueue

        try:
            await enqueue("notify_drift_breach", drift_point_id=summary.alert_point_id)
        except Exception:  # noqa: BLE001 - alert enqueue is best-effort, never break drift
            log.warning(
                "drift.alert_enqueue_failed",
                extra={"extra": {"drift_point_id": summary.alert_point_id}},
            )

    log.info("drift.job_done", extra={"extra": asdict(summary)})
    return asdict(summary)


# ---------------------------------------------------------------------------
# notify_drift_breach — Gate M2 PII-free drift-breach alert to the ML engineer
# ---------------------------------------------------------------------------


async def notify_drift_breach(
    ctx: dict[str, Any], *, drift_point_id: str
) -> dict[str, object]:
    """Send the PII-free drift-breach alert for ONE anchor point to the ML engineer, exactly once.

    Thin Arq wrapper over ``notification_service.notify_drift_breach`` (resolve platform recipients →
    PII-free body → send → flip ``notified``). A send FAILURE is logged and RE-RAISED so Arq retries
    (bounded by ``WorkerSettings.max_tries``); ``notified`` is not flipped on failure. Never inline
    in the drift-compute path.
    """
    from vigil.services import notification_service

    try:
        result = notification_service.notify_drift_breach(drift_point_id=drift_point_id)
    except Exception:  # noqa: BLE001 - log then re-raise so Arq retries (notified stays false)
        log.warning(
            "drift_notify.send_failed",
            extra={
                "extra": {
                    "drift_point_id": drift_point_id,
                    "job_try": ctx.get("job_try"),
                }
            },
        )
        raise
    log.info(
        "drift_notify.job_done",
        extra={"extra": {"drift_point_id": drift_point_id, **result}},
    )
    return result


# ---------------------------------------------------------------------------
# run_assistant_turn — the Phase-5 assistant turn (router -> Retention agent)
# ---------------------------------------------------------------------------


async def run_assistant_turn(
    ctx: dict[str, Any],
    *,
    conversation_id: str,
    user_id: str,
    content: str,
    participant_id: str | None = None,
    sponsor_id: str | None = None,
    request_id: str = "",
) -> dict[str, Any]:
    """Run one assistant turn: redact+guardrail -> router classify -> Retention agent -> event.

    Scope is RE-RESOLVED from ``user_id`` (5.4 ``agent_tool_context`` — never a serialized scope,
    never ``sponsor_bootstrap_session``), so the agent's data reach is dual-axis isolated. The LLM
    is stubbed in CI (``ctx['_llm_override']`` or ``VIGIL_LLM_STUB``). EVERY outcome (guardrail
    block, router refusal, deferred agent, answer, grounded refusal) writes ONE redacted
    ``message_events`` row (raw text never persisted). Returns the assistant turn dict.
    """
    import uuid as _uuid
    from datetime import datetime, timezone

    from vigil.agents import operations as operations_mod
    from vigil.agents import report as report_mod
    from vigil.agents import retention as retention_mod
    from vigil.agents import router as router_mod
    from vigil.agents.guardrails import guard_inbound
    from vigil.agents.llm import get_llm_client
    from vigil.agents.redaction import redact
    from vigil.agents.tools import agent_tool_context
    from vigil.agents.tracing import TurnTrace, get_tracer, safe_trace
    from vigil.repositories import observability as obs_repo

    # Router intent → agent (all three on the same spine; 5.6). Keys match router.AGENT_DEFINITIONS.
    _AGENTS = {
        "retention": retention_mod.answer,
        "report": report_mod.answer,
        "operations": operations_mod.answer,
    }

    llm = ctx.get("_llm_override") or get_llm_client()
    # Tracer is best-effort + CI-hermetic (NullTracer unless langfuse_enabled). Constructing it
    # never raises into the turn; the durable message_events write does not depend on it.
    tracer = ctx.get("_tracer_override") or get_tracer()
    model_name = get_settings().llm_model
    created_at = datetime.now(tz=timezone.utc)

    def _turn(
        text: str,
        decision: str,
        status: str,
        citations: list[Any],
        *,
        agent: str | None = None,
    ) -> dict[str, Any]:
        return {
            "conversation_id": conversation_id,
            "role": "assistant",
            "content": text,
            "guardrail_decision": decision,
            "status": status,
            # The routed agent (retention/report/operations) — already decided + persisted to
            # message_events.route_or_agent; surfaced here too. None for a guardrail/router refusal
            # (no agent dispatched). This threads an existing value; the router is unchanged.
            "agent": agent,
            "citations": citations,
            "created_at": created_at.isoformat(),
        }

    with agent_tool_context(user_id, requested_sponsor=sponsor_id) as tctx:
        scope = tctx.scope
        bound = None if scope.is_platform else scope.rls_sponsor_id(sponsor_id)
        bound_uuid = _uuid.UUID(bound) if bound else None

        def _write(
            *,
            decision: str,
            status: str,
            redacted_user: str,
            redacted_assistant: str,
            route_or_agent: str,
            citations: list[Any],
            llm_provider_model: str = "",
            latency_ms: int = 0,
            token_cost_estimate: float = 0.0,
            prompt_tokens: int = 0,
            completion_tokens: int = 0,
        ) -> None:
            # Usage defaults to honest-zero: paths with NO generation call (guardrail/router
            # refusal) persist 0 cost/latency/tokens — real provider usage (cost, latency, AND the
            # raw token counts: Gate COST-1) is threaded only where the LLM actually ran.
            # ``llm_provider_model`` falls back to the configured model id when no provider answered
            # (descriptive, not a cost claim).
            obs_repo.write_message_event(
                tctx.session,
                conversation_id=_uuid.UUID(conversation_id),
                request_id=request_id or "assistant-turn",
                sponsor_id=bound_uuid,
                role_or_guest_scope=scope.role.value,
                surface="local_assistant",
                guardrail_decision=decision,
                status=status,
                redacted_user_msg=redacted_user,
                redacted_assistant_msg=redacted_assistant,
                route_or_agent=route_or_agent,
                retrieved_chunks=citations,
                llm_provider_model=llm_provider_model or model_name,
                latency_ms=latency_ms,
                token_cost_estimate=token_cost_estimate,
                prompt_tokens=prompt_tokens,
                completion_tokens=completion_tokens,
            )
            # ADDITIVE/BEST-EFFORT trace AFTER the durable record. The TurnTrace carries ONLY the
            # same redacted text + structural metadata (no raw field exists); a Langfuse failure is
            # swallowed by safe_trace and never breaks the turn or the event row above.
            safe_trace(
                tracer,
                TurnTrace(
                    conversation_id=conversation_id,
                    request_id=request_id or "assistant-turn",
                    surface="local_assistant",
                    role_or_guest_scope=scope.role.value,
                    route_or_agent=route_or_agent,
                    guardrail_decision=decision,
                    status=status,
                    llm_provider_model=llm_provider_model or model_name,
                    latency_ms=latency_ms,
                    token_cost_estimate=token_cost_estimate,
                    redacted_user_msg=redacted_user,
                    redacted_assistant_msg=redacted_assistant,
                    retrieved_chunks=citations,
                ),
            )

        # 1. Redact BEFORE the LLM + content guardrails (5.2). Fail-loud: a redaction error blocks.
        guard = guard_inbound(content)
        if guard.result.decision == "blocked":
            refusal = (
                "This request can't be handled (it was blocked by a safety guardrail)."
            )
            _write(
                decision="blocked",
                status="refused",
                redacted_user=guard.redacted_text,
                redacted_assistant=redact(refusal),
                route_or_agent=f"guardrail:{guard.result.category}",
                citations=[],
            )
            return _turn(refusal, "blocked", "refused", [])

        # 2. Router classify (LLM, stubbed in CI). Refuse-at-router → blocked outcome, no dispatch.
        #    The router call has REAL usage (6.2b): a refusal-at-router row carries the router's own
        #    classification cost/latency (no longer honest-zero) — only a pre-router guardrail block
        #    (above, no LLM call) is genuinely zero.
        decision = router_mod.classify(llm, guard.redacted_text)
        if decision.refused:
            refusal = "I can't help with that request."
            _write(
                decision="blocked",
                status="refused",
                redacted_user=guard.redacted_text,
                redacted_assistant=redact(refusal),
                route_or_agent="router:refuse",
                citations=[],
                llm_provider_model=decision.provider_model,
                latency_ms=decision.latency_ms,
                token_cost_estimate=decision.token_cost_estimate,
                prompt_tokens=decision.prompt_tokens,
                completion_tokens=decision.completion_tokens,
            )
            return _turn(refusal, "blocked", "refused", [])

        # 3. Dispatch to the classified agent (5.6: all three wired on the same spine). Each runs
        #    under the same tctx (dual-axis scoped) + champion-only facts + doc-grounding.
        agent_fn = _AGENTS.get(decision.agent)
        if (
            agent_fn is None
        ):  # unknown agent id → fail closed (router should never return this)
            refusal = "I can't help with that request."
            _write(
                decision="blocked",
                status="refused",
                redacted_user=guard.redacted_text,
                redacted_assistant=redact(refusal),
                route_or_agent="router:unknown_agent",
                citations=[],
                llm_provider_model=decision.provider_model,
                latency_ms=decision.latency_ms,
                token_cost_estimate=decision.token_cost_estimate,
                prompt_tokens=decision.prompt_tokens,
                completion_tokens=decision.completion_tokens,
            )
            return _turn(refusal, "blocked", "refused", [])

        ans = agent_fn(tctx, llm, guard.redacted_text, participant_id=participant_id)
        redacted_answer = redact(ans.content)
        # TURN TOTAL (6.2b): an answered turn's usage = router-classification + agent-generation.
        # Cost/latency SUM across both LLM calls; the row's provider_model records the ANSWERING
        # provider (the agent generation), the dominant cost (a grounded refusal contributes honest-
        # zero agent usage, leaving just the router's real classification cost).
        _write(
            decision="allowed",
            status=ans.status,
            redacted_user=guard.redacted_text,
            redacted_assistant=redacted_answer,
            route_or_agent=f"agent:{decision.agent}",
            citations=ans.citations,
            llm_provider_model=ans.provider_model or decision.provider_model,
            latency_ms=decision.latency_ms + ans.latency_ms,
            token_cost_estimate=round(
                decision.token_cost_estimate + ans.token_cost_estimate, 6
            ),
            # COST-1: the turn's REAL token totals = router-classification + agent-generation
            # (a grounded refusal makes no agent call → just the router's tokens).
            prompt_tokens=decision.prompt_tokens + ans.prompt_tokens,
            completion_tokens=decision.completion_tokens + ans.completion_tokens,
        )
        return _turn(
            redacted_answer, "allowed", ans.status, ans.citations, agent=decision.agent
        )
