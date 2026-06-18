"""Gate 9.1 — REAL model-attribution reasons in the scoring writeback.

Proves the scorer populates GENUINE, leakage-safe per-participant attributions (occlusion over
the model's OWN inputs), honestly labelled with the method + the synthetic flag — never an
invented/fixed reason list. The empty-`top_factors=[]` gap is closed for the real LSTM champion.

Marked slow (torch) like the other B2b LSTM tests so torch never leaks into the fast/check_specs/
golden gates. Requires: live Postgres, data/models/t2d/sequence_v1.1_demo.pt.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

import pandas as pd
import pytest

pytestmark = pytest.mark.slow

_ARTIFACT_PATH = Path("data/models/t2d/sequence_v1.1_demo.pt")
_CHAMPION_MV = "sequence_v1.1:demo"


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


def _traj(pid: str, *, missed_pattern: list[bool]) -> pd.DataFrame:
    """Build an engagement frame for one participant from an attended/missed pattern."""
    rows = []
    cum = 0
    cons = 0
    for vi, missed in enumerate(missed_pattern):
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


def _load_scorer():  # type: ignore[no-untyped-def]
    import torch

    from vigil.workers.tasks import LSTMScorer

    artifact = torch.load(str(_ARTIFACT_PATH), weights_only=False)
    return LSTMScorer(artifact)


# ---------------------------------------------------------------------------
# 1. The LSTM attribution is GENUINE (tracks the inputs) + leakage-safe
# ---------------------------------------------------------------------------


def test_lstm_attribution_is_genuine_and_leakage_safe() -> None:
    from models.leakage_check import assert_no_outcome_features
    from models.t2d.synthetic_data import FORBIDDEN_FEATURES, SEQ_NUMERIC

    scorer = _load_scorer()
    pid = "11111111-1111-1111-1111-111111111111"
    participant_df = pd.DataFrame([_static_row(pid)])
    # A clear disengagement trajectory: several consecutive missed visits at the end.
    eng_df = _traj(pid, missed_pattern=[False, False, True, True, True, True])

    attrib = scorer.attributions(participant_df, eng_df)
    assert len(attrib) == 1
    reasons = attrib[0]["reasons"]

    # NON-EMPTY genuine attribution for an observed trajectory.
    assert reasons, "real LSTM attribution must be non-empty for an observed trajectory"

    names = [r["feature"] for r in reasons]
    # Every reason feature is one of the model's OWN sequence inputs — no invented feature.
    assert set(names) <= set(SEQ_NUMERIC), f"reason features not model inputs: {names}"
    # Honest method label present + a real signed numeric contribution.
    for r in reasons:
        assert r["method"] == "lstm_occlusion"
        assert isinstance(r["contribution"], float)
    assert attrib[0]["top_factors"] == names

    # LEAKAGE-SAFE (sacred): the attribution surface carries NO outcome/latent/leaky token.
    assert_no_outcome_features(names)  # must not raise
    assert "miss_probability" not in names
    assert not (set(names) & set(FORBIDDEN_FEATURES))


def test_lstm_attribution_tracks_inputs_not_fixed() -> None:
    """Two different trajectories yield different attributions — proves it is computed from the
    model's actual inputs, not a fixed/invented list."""
    scorer = _load_scorer()
    pid_a = "22222222-2222-2222-2222-222222222222"
    pid_b = "33333333-3333-3333-3333-333333333333"

    disengaged = scorer.attributions(
        pd.DataFrame([_static_row(pid_a)]),
        _traj(pid_a, missed_pattern=[False, True, True, True, True, True]),
    )[0]
    adherent = scorer.attributions(
        pd.DataFrame([_static_row(pid_b)]),
        _traj(pid_b, missed_pattern=[False, False, False, False, False, False]),
    )[0]

    # The two participants' attributions must not be identical — the method reflects the inputs.
    assert disengaged["reasons"] != adherent["reasons"], (
        "attribution is identical across distinct trajectories — it is not input-driven"
    )


# ---------------------------------------------------------------------------
# 2. score_trial writes real reasons + synthetic label (end-to-end)
# ---------------------------------------------------------------------------


def test_score_trial_writes_real_attribution_reasons(
    migrated_db: dict[str, str],
) -> None:
    from models.leakage_check import assert_no_outcome_features
    from models.t2d.synthetic_data import SEQ_NUMERIC
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
        row = scoring_repo.get_score(
            session, uuid.UUID(ids["participant_a"]), _CHAMPION_MV
        )
    assert row is not None, "no champion score row after score_trial"
    # The seeded demo participant has an observed synthetic trajectory → real reasons populated.
    assert row.top_factors, "top_factors empty — Gate 9.1 attribution not populated"
    assert row.reasons, "reasons empty — Gate 9.1 attribution not populated"

    names = [r["feature"] for r in row.reasons]
    assert set(names) <= set(SEQ_NUMERIC)
    assert_no_outcome_features(names)  # leakage-safe surface
    for r in row.reasons:
        assert r["method"] == "lstm_occlusion"
        # The synthetic flag travels WITH each reason (seeded engagement is synthetic).
        assert r["synthetic"] is True
    # top_factors mirror the reason feature names.
    assert row.top_factors == names


# ---------------------------------------------------------------------------
# 3. Champion-only on the read surface (shadow reasons not surfaced)
# ---------------------------------------------------------------------------


def test_champion_reasons_surface_shadow_not(migrated_db: dict[str, str]) -> None:
    from vigil.repositories import users as user_repo
    from vigil.repositories.session import auth_lookup_session
    from vigil.services import risk_service
    from vigil.services.scope_resolver import resolve_scope
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

    with auth_lookup_session() as session:
        user = user_repo.get_by_email(session, "coord.a@vigil.example")
        assert user is not None
        scope = resolve_scope(session, user)

    view = risk_service.get_participant_risk(scope, ids["participant_a"])
    assert view is not None
    # The surfaced risk is the CHAMPION model, and its real factors are surfaced.
    assert view.model_version == _CHAMPION_MV
    assert view.factors, "champion reasons did not surface as factors on the read path"
    # Factors are champion attributions (feature + signed contribution), never shadow rows.
    for f in view.factors:
        assert isinstance(f.feature, str) and isinstance(f.contribution, float)
