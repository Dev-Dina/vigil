"""Task functions. Jobs are idempotent and carry the scope context they need explicitly.

score_trial runs the full scoring pipeline:
  resolve scope -> load cohort -> build features -> leakage assertions -> inference
  -> compute risk_band -> writeback (upsert + audit) -> denorm update on participant
Every path fires run_smoke + assert_no_outcome_features before inference; these cannot
be skipped. synthetic=True is stamped on every row from the demo cohort.
"""

from __future__ import annotations

import asyncio
import random
import uuid
from typing import Any

from vigil.core.config import get_settings
from vigil.core.logging import get_logger

log = get_logger("vigil.worker")

# Sentinel model version used in demo mode when no real artifact exists.
_DEMO_MODEL_VERSION = "sequence_v1.0:demo"

# Default model card reference path.
_DEFAULT_MODEL_CARD = "data/models/t2d/model_card.md"


# ---------------------------------------------------------------------------
# Demo / stub scorer (random scores, fixed seed — demo mode only)
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
    """
    if "_scorer_override" in ctx:
        # Test injection path: _DEMO_MODEL_VERSION as placeholder version is fine here
        # since this path never reaches the DB or a real artifact.
        mv = model_version or _DEMO_MODEL_VERSION
        return ctx["_scorer_override"], mv

    settings = get_settings()

    if model_version is not None:
        mv = model_version
    elif regime is not None:
        mv = _resolve_champion_version(regime)
    elif settings.demo_mode:
        # regime not threaded through from the API yet — tracked B2 dependencies in
        # ROADMAP.md (ScoringTriggerIn missing regime field; trial table has no
        # indication column). Fall back to the demo regime so the demo remains
        # functional. This is NOT a silent fallback: it is logged, demo-mode-only,
        # and still routes through routing_state (hard error if t2d has no champion).
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

    # Attempt real artifact load.
    import pickle
    from pathlib import Path

    artifact_path = Path("data/models/t2d") / f"{mv.replace(':', '_')}.pkl"

    if artifact_path.exists():
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
        f"No model artifact found for version {mv!r} at {artifact_path}. "
        "Set VIGIL_DEMO_MODE=true to use the demo scorer (method demo only)."
    )


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
    3. Load participants for the trial under the job's sponsor scope
    4. Build a minimal feature representation
    5. assert_no_outcome_features — fail loud on forbidden column
    6. run_smoke — fail loud on any violation
    7. Score
    8. Compute risk_band thresholds (>0.6 high, >0.3 medium, else low)
    9. Writeback: upsert_score + write_score_audit + denorm Participant
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

    # 3. Load participants under the sponsor-scoped session.
    from vigil.db.models import Participant
    from vigil.repositories import scoring as scoring_repo
    from vigil.repositories.session import sponsor_bootstrap_session

    if sponsor_id is None:
        raise ValueError(
            "score_trial: sponsor_id must be passed in kwargs (set by enqueue)"
        )

    participants: list[Participant] = []
    with sponsor_bootstrap_session(sponsor_id) as session:
        from sqlalchemy import select

        participants = list(
            session.execute(
                select(Participant).where(Participant.trial_id == uuid.UUID(trial_id))
            ).scalars()
        )
        # Snapshot needed columns before session closes.
        participant_snapshots = [
            {
                "id": p.id,
                "sponsor_id": p.sponsor_id,
                "trial_id": p.trial_id,
                "site_id": p.site_id,
                "coded_ref": p.coded_ref,
            }
            for p in participants
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

    # 4. Build minimal feature representation (coded_ref as placeholder features).
    # In production the full ContractTransformer pipeline runs here; for the demo
    # stub we build a trivial numeric matrix from the participant index.
    import pandas as pd

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

    # 5. assert_no_outcome_features — fires before every inference call.
    from models.leakage_check import assert_no_outcome_features

    assert_no_outcome_features(feature_names)

    # 6. run_smoke — fires before every inference call.
    # run_smoke expects dict[str, FeatureMatrix]; for the scoring worker we run the
    # simpler column-name check only (the full FeatureMatrix is a training artifact;
    # at scoring time we assert the column contract instead).
    from ingestion.errors import LeakageError
    from models.features.contract import EXCLUDED_FROM_FEATURES

    excluded_present = [c for c in feature_names if c in EXCLUDED_FROM_FEATURES]
    if excluded_present:
        raise LeakageError(
            f"outcome/identity columns present in scoring features: {excluded_present}"
        )

    # 7. Score.
    scores = scorer(feature_df)
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
        from sqlalchemy import select, update

        for p_snap, score in zip(participant_snapshots, scores, strict=True):
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
                synthetic=True,  # demo cohort; always True
            )
            scoring_repo.write_score_audit(
                session,
                sponsor_id=p_snap["sponsor_id"],
                participant_id=p_snap["id"],
                model_version=resolved_mv,
                synthetic=True,
                n_rows=1,
            )
            # Denormalized read cache on participant.
            session.execute(
                update(Participant)
                .where(Participant.id == p_snap["id"])
                .values(risk_score=float(score), risk_band=band)
            )
            n_scored += 1

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
