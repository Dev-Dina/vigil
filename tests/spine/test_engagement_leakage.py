"""Engagement table leakage invariants 6, 7, 10 (specs/scoring.md § Leakage-test invariants).

Invariants 8 (synthetic propagation to score) and 9 (single-path build_tensors) require a
running scorer (sequence LSTM artifact + wired inference). They are B2b obligations and are
NOT attempted here. Their absence is intentional, not a gap.

These tests require live Postgres (RLS is a DB feature). They skip when no Postgres is
reachable (same pattern as the existing leakage suite).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest


# ---------------------------------------------------------------------------
# Invariant 6 — Cross-tenant isolation on engagement rows
# ---------------------------------------------------------------------------


def test_engagement_invisible_to_other_sponsor(migrated_db: dict[str, str]) -> None:
    """Engagement rows written under Sponsor A are invisible to a Sponsor B session."""
    from sqlalchemy import select

    from vigil.db.models import Engagement, Participant
    from vigil.repositories import users as user_repo
    from vigil.repositories.session import (
        auth_lookup_session,
        scoped_session,
        sponsor_bootstrap_session,
    )
    from vigil.services.scope_resolver import resolve_scope

    ids = migrated_db
    sponsor_a_id = uuid.UUID(ids["sponsor_a"])
    trial_a_id = uuid.UUID(ids["trial_a"])
    site_a_id = uuid.UUID(ids["site_a"])

    # Create a fresh isolated participant under Sponsor A for this test so we don't
    # pollute the seeded demo participant's trajectory (which other tests verify is coherent).
    iso_participant_id = uuid.uuid4()
    with sponsor_bootstrap_session(str(sponsor_a_id)) as session:
        session.add(
            Participant(
                id=iso_participant_id,
                sponsor_id=sponsor_a_id,
                trial_id=trial_a_id,
                site_id=site_a_id,
                coded_ref="inv6-isolation-probe",
            )
        )
        session.flush()

    eng_id = uuid.uuid4()
    with sponsor_bootstrap_session(str(sponsor_a_id)) as session:
        row = Engagement(
            id=eng_id,
            sponsor_id=sponsor_a_id,
            participant_id=iso_participant_id,
            trial_id=trial_a_id,
            site_id=site_a_id,
            visit_index=0,
            visit_timestamp=datetime(2025, 1, 1, tzinfo=timezone.utc),
            attended=True,
            missed=False,
            cumulative_missed=0,
            consecutive_missed=0,
            synthetic=True,
        )
        session.add(row)
        session.flush()

    # Resolve Sponsor B scope exactly as login would.
    with auth_lookup_session() as session:
        user_b = user_repo.get_by_email(session, "coord.b@vigil.example")
        assert user_b is not None
        scope_b = resolve_scope(session, user_b)

    # Sponsor B session must see zero engagement rows for Sponsor A's participant.
    with scoped_session(scope_b) as session:
        fetched = session.get(Engagement, eng_id)
        assert fetched is None, (
            "Sponsor A engagement row leaked to Sponsor B via direct get"
        )

        rows = list(
            session.execute(
                select(Engagement).where(
                    Engagement.participant_id == iso_participant_id
                )
            ).scalars()
        )
        assert rows == [], (
            f"Sponsor A engagement rows leaked to Sponsor B via query: {rows}"
        )


# ---------------------------------------------------------------------------
# Invariant 4 — assert_no_outcome_features blocks miss_probability (shared guard)
# ---------------------------------------------------------------------------


def test_miss_probability_is_forbidden_by_shared_guard() -> None:
    """assert_no_outcome_features raises loudly when miss_probability is present.

    specs/scoring.md § Leakage-test invariants inv 4: miss_probability (the synthetic
    generator's latent hazard) must be blocked by the shared guard before B2b wires
    engagement features through the scoring path.
    """
    from ingestion.errors import LeakageError
    from models.leakage_check import assert_no_outcome_features

    with pytest.raises(LeakageError):
        assert_no_outcome_features(["visit_index", "attended", "miss_probability"])


def test_miss_probability_alone_is_forbidden() -> None:
    """miss_probability alone triggers the guard (not dependent on other forbidden columns)."""
    from ingestion.errors import LeakageError
    from models.leakage_check import assert_no_outcome_features

    with pytest.raises(LeakageError):
        assert_no_outcome_features(["miss_probability"])


def test_legitimate_engagement_features_pass_guard() -> None:
    """Legitimate engagement feature names do not trigger assert_no_outcome_features."""
    from models.leakage_check import assert_no_outcome_features

    # These are the actual NUMERIC_FEATURES from models/t2d/survival.py — must not raise.
    assert_no_outcome_features(
        [
            "visit_index",
            "attended",
            "missed",
            "cumulative_missed",
            "consecutive_missed",
            "age_years",
            "hba1c_pct",
            "bmi",
            "n_sites",
            "planned_duration_days",
        ]
    )


# ---------------------------------------------------------------------------
# Invariant 7 — Feature-time assertion fires on post-horizon engagement
# ---------------------------------------------------------------------------


def test_feature_time_raises_on_future_visit(migrated_db: dict[str, str]) -> None:
    """assert_feature_time_before_t raises when a row has feature_time >= t (loud)."""
    import pandas as pd
    from ingestion.errors import LeakageError
    from models.leakage_check import assert_feature_time_before_t

    decision_time = datetime(2025, 6, 1, tzinfo=timezone.utc)
    future_visit = datetime(2025, 6, 2, tzinfo=timezone.utc)
    past_visit = datetime(2025, 5, 31, tzinfo=timezone.utc)

    # A row with visit_timestamp >= decision_time must cause a hard raise.
    df_leaking = pd.DataFrame(
        {
            "decision_time": [decision_time, decision_time],
            "visit_timestamp": [past_visit, future_visit],
        }
    )
    with pytest.raises(LeakageError, match="future leakage"):
        assert_feature_time_before_t(
            df_leaking, t_col="decision_time", feature_time_col="visit_timestamp"
        )


def test_feature_time_passes_on_past_visits_only(
    migrated_db: dict[str, str],
) -> None:
    """assert_feature_time_before_t does NOT raise when all feature timestamps < t."""
    import pandas as pd
    from models.leakage_check import assert_feature_time_before_t

    decision_time = datetime(2025, 6, 1, tzinfo=timezone.utc)
    df_clean = pd.DataFrame(
        {
            "decision_time": [decision_time, decision_time],
            "visit_timestamp": [
                datetime(2025, 5, 30, tzinfo=timezone.utc),
                datetime(2025, 5, 31, tzinfo=timezone.utc),
            ],
        }
    )
    # Must not raise.
    assert_feature_time_before_t(
        df_clean, t_col="decision_time", feature_time_col="visit_timestamp"
    )


def test_feature_time_raises_on_equal_timestamps(
    migrated_db: dict[str, str],
) -> None:
    """assert_feature_time_before_t is strictly-before: equal timestamps also raise."""
    import pandas as pd
    from ingestion.errors import LeakageError
    from models.leakage_check import assert_feature_time_before_t

    t = datetime(2025, 6, 1, tzinfo=timezone.utc)
    df_equal = pd.DataFrame({"decision_time": [t], "visit_timestamp": [t]})
    with pytest.raises(LeakageError):
        assert_feature_time_before_t(
            df_equal, t_col="decision_time", feature_time_col="visit_timestamp"
        )


# ---------------------------------------------------------------------------
# Invariant 10 — Covariate provenance flags mandatory and honest
# ---------------------------------------------------------------------------

_IMPUTED_CAPABLE = ("age_years", "hba1c_pct", "bmi")


def test_imputed_capable_columns_have_companion_flags_in_orm(
    migrated_db: dict[str, str],
) -> None:
    """Each imputed-capable covariate has its *_baseline_imputed companion on the ORM."""
    from vigil.db.models import Participant

    mapper = Participant.__mapper__
    col_names = {c.key for c in mapper.columns}
    for col in _IMPUTED_CAPABLE:
        assert col in col_names, f"ORM Participant is missing covariate column '{col}'"
        flag = f"{col}_baseline_imputed"
        assert flag in col_names, (
            f"ORM Participant is missing provenance flag '{flag}' "
            f"(companion to '{col}', invariant 10)"
        )


def test_imputed_capable_columns_have_companion_flags_in_schema(
    migrated_db: dict[str, str],
) -> None:
    """Each imputed-capable covariate has its *_baseline_imputed column in the live schema."""
    from sqlalchemy import inspect as sa_inspect
    from vigil.db.engine import get_engine

    eng = get_engine()
    insp = sa_inspect(eng)
    col_names = {c["name"] for c in insp.get_columns("participant")}
    for col in _IMPUTED_CAPABLE:
        assert col in col_names, (
            f"DB participant table missing covariate column '{col}'"
        )
        flag = f"{col}_baseline_imputed"
        assert flag in col_names, (
            f"DB participant table missing provenance flag '{flag}' (invariant 10)"
        )


def test_planned_duration_days_flag_absent_from_participant(
    migrated_db: dict[str, str],
) -> None:
    """planned_duration_days is trial-level; its flag must NOT exist on participant."""
    from sqlalchemy import inspect as sa_inspect
    from vigil.db.engine import get_engine

    eng = get_engine()
    insp = sa_inspect(eng)
    col_names = {c["name"] for c in insp.get_columns("participant")}
    assert "planned_duration_days_baseline_imputed" not in col_names, (
        "planned_duration_days_baseline_imputed found on participant table — "
        "planned_duration_days is trial-level and must not have a participant flag "
        "(scoring.md spec correction B2a-spec-2 decision c)"
    )


def test_provenance_flag_both_directions(migrated_db: dict[str, str]) -> None:
    """A covariate with imputed=True and one with imputed=False are both written and
    read back correctly — the flag cannot default-stamp one direction only."""
    from vigil.db.models import Participant
    from vigil.repositories.session import sponsor_bootstrap_session

    ids = migrated_db
    sponsor_a_id = uuid.UUID(ids["sponsor_a"])
    trial_a_id = uuid.UUID(ids["trial_a"])
    site_a_id = uuid.UUID(ids["site_a"])

    # Write two participants: one with imputed BMI, one with real BMI.
    pid_imputed = uuid.uuid4()
    pid_real = uuid.uuid4()

    with sponsor_bootstrap_session(str(sponsor_a_id)) as session:
        session.add(
            Participant(
                id=pid_imputed,
                sponsor_id=sponsor_a_id,
                trial_id=trial_a_id,
                site_id=site_a_id,
                coded_ref="inv10-imputed",
                bmi=27.5,
                bmi_baseline_imputed=True,
            )
        )
        session.add(
            Participant(
                id=pid_real,
                sponsor_id=sponsor_a_id,
                trial_id=trial_a_id,
                site_id=site_a_id,
                coded_ref="inv10-real",
                bmi=24.0,
                bmi_baseline_imputed=False,
            )
        )
        session.flush()

    with sponsor_bootstrap_session(str(sponsor_a_id)) as session:
        p_imp = session.get(Participant, pid_imputed)
        assert p_imp is not None
        assert p_imp.bmi_baseline_imputed is True, (
            "Imputed participant: bmi_baseline_imputed should be True"
        )

        p_real = session.get(Participant, pid_real)
        assert p_real is not None
        assert p_real.bmi_baseline_imputed is False, (
            "Real participant: bmi_baseline_imputed should be False"
        )


def test_baseline_imputed_flags_excluded_from_features(
    migrated_db: dict[str, str],
) -> None:
    """*_baseline_imputed flags are in EXCLUDED_FROM_FEATURES — never model predictors."""
    from models.features.contract import EXCLUDED_FROM_FEATURES

    for col in _IMPUTED_CAPABLE:
        flag = f"{col}_baseline_imputed"
        assert flag in EXCLUDED_FROM_FEATURES, (
            f"'{flag}' is missing from EXCLUDED_FROM_FEATURES — "
            "provenance flags must never be model predictors (invariant 10)"
        )


def test_baseline_imputed_flags_in_excluded_from_features(
    migrated_db: dict[str, str],
) -> None:
    """*_baseline_imputed flags are in EXCLUDED_FROM_FEATURES (provenance, not predictors).

    The ContractTransformer excludes EXCLUDED_FROM_FEATURES columns before building the
    feature matrix; verifying membership here guarantees the flags can never reach the model
    as predictors, consistent with scoring.md § Inv 10.
    """
    from models.features.contract import EXCLUDED_FROM_FEATURES

    for col in _IMPUTED_CAPABLE:
        flag = f"{col}_baseline_imputed"
        assert flag in EXCLUDED_FROM_FEATURES, (
            f"'{flag}' is missing from EXCLUDED_FROM_FEATURES — "
            "provenance flags must never be model predictors (scoring.md invariant 10)"
        )
