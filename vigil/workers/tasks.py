"""Task functions. Jobs are idempotent and carry the scope context they need explicitly.

score_trial runs the full scoring pipeline:
  resolve scope -> load cohort + engagement -> temporal guard -> feature guards
  -> inference (LSTM or demo) -> compute risk_band -> writeback (upsert + audit)
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
_DEMO_MODEL_VERSION = "sequence_v1.0:demo"

# Default model card reference path.
_DEFAULT_MODEL_CARD = "data/models/t2d/model_card.md"


# ---------------------------------------------------------------------------
# LSTM scorer — wraps a loaded .pt artifact for per-participant scoring.
# All torch imports are deferred to __init__ / __call__ so importing this
# module never loads torch (light-suite isolation invariant).
# ---------------------------------------------------------------------------


class LSTMScorer:
    """Wraps a loaded sequence_v1.0_demo.pt artifact for live inference.

    __init__ and __call__ import torch lazily so the module-level import of
    vigil.workers.tasks does NOT load torch (light-suite isolation).
    Invariant 9: feature assembly uses models.t2d.sequence._seq_feature_frame,
    the same function used at training time — no parallel scoring-only builder.
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
        # Suppress unused-import warning; torch is needed for no-grad context.
        self._torch = torch

    def __call__(self, participant_df: Any, eng_df: Any) -> list[float]:
        """Score each participant from their engagement trajectory.

        participant_df: one row per participant with all 8 static features.
          Columns: participant_id, age_years, hba1c_pct, bmi, sex, arm_type,
                   n_sites, planned_duration_days, phase.
        eng_df: engagement rows for all participants; must have participant_id col.
        Returns: list[float] — final-step risk probability per participant.
        """
        import numpy as np  # deferred

        import models.t2d.sequence as _seq_mod  # noqa: PLC0415

        t_max = self._cfg.max_visits
        seq_dim = self._cfg.__class__  # sentinel; resolved from seq_means shape
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

        torch = self._torch
        with torch.no_grad():
            logits = self._model(
                torch.from_numpy(seq),
                torch.from_numpy(static_input),
            )  # (N, T) logits
            probs = torch.sigmoid(logits).numpy()  # (N, T)

        scores: list[float] = []
        for i in range(n):
            n_v = int(last_obs[i])
            scores.append(float(probs[i, n_v - 1]) if n_v > 0 else 0.5)
        return scores


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
    1. Jitter (idempotent retry safety)
    2. Resolve scorer + model version (fail loud if no artifact in prod)
    3. Load participants + trial metadata + all engagement for the trial
    4. assert_feature_time_before_t (temporal guard; fires when engagement exists)
    5. assert_no_outcome_features on sequence feature names (constant guard)
    6. EXCLUDED_FROM_FEATURES check (same features)
    7. Score (LSTMScorer or _demo_scorer)
    8. Compute risk_band thresholds (>0.6 high, >0.3 medium, else low)
    9. Writeback: upsert_score + write_score_audit + denorm Participant
       synthetic flag = logical-OR over participant's engagement rows (inv 8)
    10. Return summary dict (no PII, no risk values in log)
    """
    # 1. Jitter — exponential backoff safety across retries.
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

    # 9. Writeback.
    n_scored = 0
    with sponsor_bootstrap_session(sponsor_id) as session:
        from sqlalchemy import update

        for p_snap, score, synth_flag in zip(
            participant_snapshots, scores, synth_flags, strict=True
        ):
            band = _band(float(score))
            scoring_repo.upsert_score(
                session,
                participant_id=p_snap["id"],
                sponsor_id=p_snap["sponsor_id"],
                trial_id=p_snap["trial_id"],
                site_id=p_snap["site_id"],
                risk_score=float(score),
                risk_band=band,
                top_factors=[],
                reasons=[],
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
            n_scored += 1

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

            # Shadow synthetic flag: same logical-OR over engagement rows as champion (inv 8).
            shadow_pid_synth: dict[str, bool] = {}
            for e in eng_snapshots:
                pid_str = str(e["participant_id"])
                shadow_pid_synth[pid_str] = shadow_pid_synth.get(
                    pid_str, False
                ) or bool(e["synthetic"])

            with sponsor_bootstrap_session(sponsor_id) as session:
                for p_snap, sh_score in zip(
                    participant_snapshots, shadow_scores, strict=True
                ):
                    sh_synth = shadow_pid_synth.get(str(p_snap["id"]), True)
                    scoring_repo.upsert_score(
                        session,
                        participant_id=p_snap["id"],
                        sponsor_id=p_snap["sponsor_id"],
                        trial_id=p_snap["trial_id"],
                        site_id=p_snap["site_id"],
                        risk_score=float(sh_score),
                        risk_band=_band(float(sh_score)),
                        top_factors=[],
                        reasons=[],
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
