"""B2b scoring invariants: sequence LSTM wired to score_trial (specs/scoring.md § B2b).

Invariants covered:
  Inv 8  — synthetic=True engagement propagates to participant_score.synthetic.
  Inv 9  — scoring path calls training-time _seq_feature_frame (single builder).
  Fidelity — reloaded .pt artifact reproduces trained PR-AUC within tolerance.
  Scoreability — score_trial returns real LSTM scores (not random) for demo trial.

All tests are marked slow (torch dependency) so they are excluded from the fast suite
(TORCH ISOLATION: torch must not leak into check_specs / golden / fast-suite gates).
Requires: live Postgres, data/models/t2d/sequence_v1.1_demo.pt.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pytest

pytestmark = pytest.mark.slow

_ARTIFACT_PATH = Path("data/models/t2d/sequence_v1.1_demo.pt")
_TRAINED_PR_AUC = 0.3390398896276865  # from data/models/t2d/sequence_metrics.json
_FIDELITY_TOLERANCE = 0.001


# ---------------------------------------------------------------------------
# 1. Artifact file exists
# ---------------------------------------------------------------------------


def test_artifact_file_exists() -> None:
    """sequence_v1.1_demo.pt must exist at the canonical path before scoring can run."""
    assert _ARTIFACT_PATH.exists(), (
        f"LSTM artifact not found at {_ARTIFACT_PATH}. "
        "Run: uv run python scripts/persist_sequence_artifact.py"
    )


# ---------------------------------------------------------------------------
# 2. Fidelity: reloaded artifact reproduces trained PR-AUC
# ---------------------------------------------------------------------------


def test_fidelity_reloaded_pr_auc() -> None:
    """Reloaded .pt artifact reproduces the trained test PR-AUC within 0.001.

    HERMETIC: the test fold is the artifact's own frozen ``test_nct_ids`` (public NCT
    identifiers, no PHI) scored against the COMMITTED synthetic parquet — it does NOT
    rebuild the cohort split from gitignored raw AACT. This proves the reloaded weights
    are the actual trained network (reproduces ~0.339 PR-AUC), not a re-init.
    """
    import torch

    from models.t2d.metrics import classifier_panel
    from models.t2d.sequence import (
        LSTMClassifier,
        _decision_point_eval,
        _predict_steps,
        build_tensors,
    )
    from models.t2d.synthetic_data import load_synthetic_t2d

    artifact = torch.load(str(_ARTIFACT_PATH), weights_only=False)
    assert "test_nct_ids" in artifact, (
        "artifact missing frozen test_nct_ids — regenerate via "
        "`uv run python -m scripts.persist_sequence_artifact`"
    )

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
    pr_auc = classifier_panel(y, pr)["pr_auc"]

    delta = abs(pr_auc - _TRAINED_PR_AUC)
    assert delta <= _FIDELITY_TOLERANCE, (
        f"Reloaded PR-AUC {pr_auc:.4f} deviates {delta:.4f} from trained "
        f"{_TRAINED_PR_AUC:.4f} (tolerance {_FIDELITY_TOLERANCE}) — "
        "artifact state_dict or scaler mismatch"
    )


# ---------------------------------------------------------------------------
# 3. Scoreability: score_trial returns real LSTM scores for demo trial
# ---------------------------------------------------------------------------


def test_scoreability_score_trial_end_to_end(migrated_db: dict[str, str]) -> None:
    """score_trial with LSTM artifact returns one score per participant, all in [0, 1]."""
    from vigil.workers.tasks import score_trial

    ids = migrated_db
    result = asyncio.get_event_loop().run_until_complete(
        score_trial(
            {"job_try": 0},
            trial_id=ids["trial_a"],
            sponsor_id=ids["sponsor_a"],
            model_version=None,
            regime="t2d",
        )
    )
    assert result["n_scored"] == 1, (
        f"Expected 1 participant scored, got {result['n_scored']}"
    )
    assert result["model_version"] == "sequence_v1.1:demo"

    # Verify score is in range via participant read-back.
    from vigil.db.models import Participant
    from vigil.repositories.session import sponsor_bootstrap_session

    with sponsor_bootstrap_session(ids["sponsor_a"]) as session:
        p = session.get(Participant, uuid.UUID(ids["participant_a"]))
    assert p is not None
    assert 0.0 <= p.risk_score <= 1.0, f"risk_score {p.risk_score} out of range"
    # LSTM scores are not identical to random(seed=42)=0.6394... value for n=1
    random_seed42_n1 = 0.6394267984578837
    assert abs(p.risk_score - random_seed42_n1) > 1e-6, (
        "risk_score matches random(seed=42) — LSTM not wired, still using _demo_scorer"
    )


# ---------------------------------------------------------------------------
# 4. Invariant 8: synthetic engagement propagates to score.synthetic
# ---------------------------------------------------------------------------


def test_invariant_8_synthetic_propagation(migrated_db: dict[str, str]) -> None:
    """Inv 8: all seeded engagement is synthetic=True → participant_score.synthetic=True."""
    from vigil.repositories import scoring as scoring_repo
    from vigil.repositories.session import sponsor_bootstrap_session
    from vigil.workers.tasks import score_trial

    ids = migrated_db
    asyncio.get_event_loop().run_until_complete(
        score_trial(
            {"job_try": 0},
            trial_id=ids["trial_a"],
            sponsor_id=ids["sponsor_a"],
            model_version=None,
            regime="t2d",
        )
    )

    with sponsor_bootstrap_session(ids["sponsor_a"]) as session:
        rows = scoring_repo.list_scores_for_participant(
            session, uuid.UUID(ids["participant_a"])
        )

    score_row = next((r for r in rows if r.model_version == "sequence_v1.1:demo"), None)
    assert score_row is not None, (
        "No score row for sequence_v1.1:demo after score_trial"
    )
    assert score_row.synthetic is True, (
        f"Invariant 8 violated: seeded engagement is synthetic=True but "
        f"participant_score.synthetic={score_row.synthetic}"
    )


# ---------------------------------------------------------------------------
# 5. Invariant 9: scoring path uses training-time _seq_feature_frame
# ---------------------------------------------------------------------------


def test_invariant_9_single_feature_builder(migrated_db: dict[str, str]) -> None:
    """Inv 9: LSTMScorer calls models.t2d.sequence._seq_feature_frame (same as training)."""
    import unittest.mock as mock

    import torch

    import models.t2d.sequence as seq_module
    from vigil.workers.tasks import LSTMScorer

    artifact = torch.load(str(_ARTIFACT_PATH), weights_only=False)
    scorer = LSTMScorer(artifact)

    training_ff = seq_module._seq_feature_frame

    ids = migrated_db
    from vigil.db.models import Engagement
    from vigil.repositories.session import sponsor_bootstrap_session
    from sqlalchemy import select
    import pandas as pd

    with sponsor_bootstrap_session(ids["sponsor_a"]) as session:
        eng_rows = list(
            session.execute(
                select(Engagement).where(
                    Engagement.participant_id == uuid.UUID(ids["participant_a"])
                )
            ).scalars()
        )

    eng_df = pd.DataFrame(
        [
            {
                "participant_id": str(e.participant_id),
                "visit_index": e.visit_index,
                "visited_timestamp": e.visit_timestamp,
                "attended": e.attended,
                "missed": e.missed,
                "cumulative_missed": e.cumulative_missed,
                "consecutive_missed": e.consecutive_missed,
                "visit_timestamp": e.visit_timestamp,
            }
            for e in eng_rows
        ]
    )

    participant_df = pd.DataFrame(
        [
            {
                "participant_id": ids["participant_a"],
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

    with mock.patch.object(
        seq_module, "_seq_feature_frame", wraps=training_ff
    ) as mock_ff:
        scorer(participant_df, eng_df)

    assert mock_ff.called, (
        "Invariant 9 violated: LSTMScorer did not call "
        "models.t2d.sequence._seq_feature_frame — a parallel feature builder may exist"
    )


# ---------------------------------------------------------------------------
# 6. Invariant 7 (live): temporal guard fires inside score_trial
# ---------------------------------------------------------------------------


def test_invariant_7_temporal_guard_live(migrated_db: dict[str, str]) -> None:
    """Inv 7: score_trial raises LeakageError when a future-visit engagement exists.

    Uses a fresh dedicated trial (never shared with other tests) so this assertion
    is order-independent: no other test can see or corrupt this trial's engagement rows.
    """
    from datetime import datetime, timezone

    from ingestion.errors import LeakageError
    from vigil.db.models import Engagement, Participant, Trial
    from vigil.repositories.session import sponsor_bootstrap_session
    from vigil.workers.tasks import score_trial

    ids = migrated_db
    sponsor_id = uuid.UUID(ids["sponsor_a"])
    site_id = uuid.UUID(ids["site_a"])

    # Fresh trial owned by sponsor_a — not shared with any other test.
    fresh_trial_id = uuid.uuid4()
    fresh_participant_id = uuid.uuid4()

    with sponsor_bootstrap_session(str(sponsor_id)) as session:
        session.add(
            Trial(
                id=fresh_trial_id,
                sponsor_id=sponsor_id,
                name="inv7-guard-probe-trial",
            )
        )
        session.flush()
        session.add(
            Participant(
                id=fresh_participant_id,
                sponsor_id=sponsor_id,
                trial_id=fresh_trial_id,
                site_id=site_id,
                coded_ref="inv7-future-guard-probe",
            )
        )
        session.flush()
        session.add(
            Engagement(
                id=uuid.uuid4(),
                sponsor_id=sponsor_id,
                participant_id=fresh_participant_id,
                trial_id=fresh_trial_id,
                site_id=site_id,
                visit_index=9999,
                visit_timestamp=datetime(2099, 1, 1, tzinfo=timezone.utc),
                attended=True,
                missed=False,
                cumulative_missed=0,
                consecutive_missed=0,
                synthetic=True,
            )
        )
        session.flush()

    with pytest.raises(LeakageError, match="future leakage"):
        asyncio.get_event_loop().run_until_complete(
            score_trial(
                {"job_try": 0},
                trial_id=str(fresh_trial_id),
                sponsor_id=str(sponsor_id),
                model_version=None,
                regime="t2d",
            )
        )


# ---------------------------------------------------------------------------
# 7. LSTMScorer loaded from artifact (not _demo_scorer)
# ---------------------------------------------------------------------------


def test_lstm_scorer_loaded_not_demo_scorer(migrated_db: dict[str, str]) -> None:
    """_load_scorer returns LSTMScorer (real artifact), not _demo_scorer (rng fallback)."""
    from vigil.workers.tasks import LSTMScorer, _load_scorer, _demo_scorer

    scorer, mv = _load_scorer("sequence_v1.1:demo", ctx={})
    assert isinstance(scorer, LSTMScorer), (
        f"Expected LSTMScorer from artifact, got {type(scorer).__name__} "
        "(artifact may be missing or _load_scorer fell through to _demo_scorer)"
    )
    assert scorer is not _demo_scorer, (
        "_load_scorer returned _demo_scorer; real artifact not loaded"
    )
    assert mv == "sequence_v1.1:demo"
