"""B4 — cohort provenance is data-driven and champion-only.

Closes carried debt: CohortRow.synthetic and top_factors were hardcoded in cohort_service
(synthetic=True always — a spec violation of api.md). They now come from the CHAMPION
participant_score row, read through the champion allowlist (B2c surfacing preserved).

Real Postgres via migrated_db. Fresh participants per assertion (order-independent).
"""

from __future__ import annotations

import json
import uuid
from pathlib import Path

from vigil.core.scope import Scope, ScopeTuple
from vigil.domain import Role

_CHAMPION_MV = "sequence_v1.0:demo"
_SHADOW_MV = "structural_v1.0:t2d"
_CARD = "data/models/t2d/model_card.md"
_SHADOW_CARD = "data/models/t2d/model_card_structural.md"
_METRICS = Path("data/models/baselines/metrics.json")


def _coordinator_scope(sponsor_id: str) -> Scope:
    return Scope(
        user_id="00000000-0000-0000-0000-0000000000c0",
        role=Role.COORDINATOR,
        home_sponsor_id=sponsor_id,
        home_cro_id=None,
        tuples=(ScopeTuple(sponsor_id=sponsor_id),),
        scope_ver=1,
    )


def _make_participant(ids: dict[str, str], coded_ref: str) -> uuid.UUID:
    from vigil.db.models import Participant
    from vigil.repositories.session import sponsor_bootstrap_session

    pid = uuid.uuid4()
    with sponsor_bootstrap_session(ids["sponsor_a"]) as session:
        session.add(
            Participant(
                id=pid,
                sponsor_id=uuid.UUID(ids["sponsor_a"]),
                trial_id=uuid.UUID(ids["trial_a"]),
                site_id=uuid.UUID(ids["site_a"]),
                coded_ref=coded_ref,
            )
        )
        session.flush()
    return pid


def _write_score(
    ids: dict[str, str],
    pid: uuid.UUID,
    *,
    model_version: str,
    model_card_ref: str,
    synthetic: bool,
    top_factors: list[str],
) -> None:
    from vigil.repositories import scoring as scoring_repo
    from vigil.repositories.session import sponsor_bootstrap_session

    with sponsor_bootstrap_session(ids["sponsor_a"]) as session:
        scoring_repo.append_score(
            session,
            participant_id=pid,
            sponsor_id=uuid.UUID(ids["sponsor_a"]),
            trial_id=uuid.UUID(ids["trial_a"]),
            site_id=uuid.UUID(ids["site_a"]),
            risk_score=0.5,
            risk_band="medium",
            top_factors=top_factors,
            reasons=[],
            model_version=model_version,
            model_card_ref=model_card_ref,
            synthetic=synthetic,
        )


def _cohort_row(ids: dict[str, str], pid: uuid.UUID):  # type: ignore[no-untyped-def]
    from vigil.services import cohort_service

    rows = cohort_service.list_cohort(_coordinator_scope(ids["sponsor_a"]), limit=500)
    return next((r for r in rows if r.participant_id == str(pid)), None)


# ---------------------------------------------------------------------------
# 1. synthetic is data-driven — BOTH directions (no default-stamping)
# ---------------------------------------------------------------------------


def test_synthetic_data_driven_true(migrated_db: dict[str, str]) -> None:
    pid = _make_participant(migrated_db, "b4-synth-true")
    _write_score(
        migrated_db,
        pid,
        model_version=_CHAMPION_MV,
        model_card_ref=_CARD,
        synthetic=True,
        top_factors=[],
    )
    row = _cohort_row(migrated_db, pid)
    assert row is not None
    assert row.synthetic is True


def test_synthetic_data_driven_false(migrated_db: dict[str, str]) -> None:
    pid = _make_participant(migrated_db, "b4-synth-false")
    _write_score(
        migrated_db,
        pid,
        model_version=_CHAMPION_MV,
        model_card_ref=_CARD,
        synthetic=False,
        top_factors=[],
    )
    row = _cohort_row(migrated_db, pid)
    assert row is not None
    assert row.synthetic is False, (
        "synthetic=False champion row surfaced as True — field is default-stamped, not data-driven"
    )


# ---------------------------------------------------------------------------
# 2. Champion-only preserved: shadow synthetic/factors never surface (B2c regression)
# ---------------------------------------------------------------------------


def test_champion_only_preserved_against_shadow(migrated_db: dict[str, str]) -> None:
    pid = _make_participant(migrated_db, "b4-champ-only")
    # Champion: synthetic=True, factor "champ".
    _write_score(
        migrated_db,
        pid,
        model_version=_CHAMPION_MV,
        model_card_ref=_CARD,
        synthetic=True,
        top_factors=["champ_factor"],
    )
    # Shadow: deliberately DIFFERENT synthetic + factors. Must never surface.
    _write_score(
        migrated_db,
        pid,
        model_version=_SHADOW_MV,
        model_card_ref=_SHADOW_CARD,
        synthetic=False,
        top_factors=["shadow_factor"],
    )
    row = _cohort_row(migrated_db, pid)
    assert row is not None
    assert row.synthetic is True, (
        "shadow synthetic flag punched through champion-only surfacing"
    )
    assert row.top_factors == ["champ_factor"], (
        f"shadow factors surfaced via cohort: {row.top_factors!r} — champion-only BROKEN"
    )


# ---------------------------------------------------------------------------
# 3. top_factors data-driven (present matches; empty stays empty — not fabricated)
# ---------------------------------------------------------------------------


def test_top_factors_data_driven(migrated_db: dict[str, str]) -> None:
    pid_with = _make_participant(migrated_db, "b4-factors")
    _write_score(
        migrated_db,
        pid_with,
        model_version=_CHAMPION_MV,
        model_card_ref=_CARD,
        synthetic=True,
        top_factors=["missed_visits", "hba1c_trend"],
    )
    row = _cohort_row(migrated_db, pid_with)
    assert row is not None
    assert row.top_factors == ["missed_visits", "hba1c_trend"]

    pid_empty = _make_participant(migrated_db, "b4-factors-empty")
    _write_score(
        migrated_db,
        pid_empty,
        model_version=_CHAMPION_MV,
        model_card_ref=_CARD,
        synthetic=True,
        top_factors=[],
    )
    row_empty = _cohort_row(migrated_db, pid_empty)
    assert row_empty is not None
    assert row_empty.top_factors == [], "empty champion factors were fabricated"


# ---------------------------------------------------------------------------
# 4. Pan-indication metrics JSON records test base rate (Finding 2 closed)
# ---------------------------------------------------------------------------


def test_metrics_records_test_base_rate() -> None:
    metrics = json.loads(_METRICS.read_text(encoding="utf-8"))
    test_block = metrics["test"]
    assert "positive_prevalence" in test_block, (
        "metrics.json test block missing positive_prevalence — PR-AUC not readable vs chance"
    )
    prevalence = test_block["positive_prevalence"]
    assert isinstance(prevalence, (int, float))
    assert 0.0 < prevalence < 1.0, f"positive_prevalence {prevalence} not in (0,1)"
