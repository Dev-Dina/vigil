"""Seed-bridge invariants: synthetic T2D engagement → demo participant UUIDs (B2a-2).

Mapping rule: synthetic_pid = uint32(MD5(uuid.bytes + b"seed-bridge-v1")[:4]) % n_participants.
Reference epoch: 2023-01-01 UTC. miss_probability withheld (latent hazard, inv 4).

These tests require live Postgres (RLS is a DB feature); they skip when no Postgres is
reachable — same pattern as the rest of the spine suite.
"""

from __future__ import annotations

import uuid

import pandas as pd


# ---------------------------------------------------------------------------
# Seeded engagement rows exist and are well-formed
# ---------------------------------------------------------------------------


def test_demo_participants_have_engagement_rows(migrated_db: dict[str, str]) -> None:
    """Both demo participants have engagement rows after seed."""
    from sqlalchemy import func, select

    from vigil.db.models import Engagement
    from vigil.repositories.session import sponsor_bootstrap_session

    ids = migrated_db
    for pid_key, sid_key in [
        ("participant_a", "sponsor_a"),
        ("participant_b", "sponsor_b"),
    ]:
        pid = uuid.UUID(ids[pid_key])
        sid = uuid.UUID(ids[sid_key])
        with sponsor_bootstrap_session(str(sid)) as session:
            count = session.scalar(
                select(func.count())
                .select_from(Engagement)
                .where(Engagement.participant_id == pid)
            )
        assert count is not None and count > 0, (
            f"{pid_key}: no engagement rows after seed"
        )


def test_engagement_sequence_coherent(migrated_db: dict[str, str]) -> None:
    """visit_index is contiguous 0..N-1 with no gaps for each seeded demo participant."""
    from sqlalchemy import select

    from vigil.db.models import Engagement
    from vigil.repositories.session import sponsor_bootstrap_session

    ids = migrated_db
    for pid_key, sid_key in [
        ("participant_a", "sponsor_a"),
        ("participant_b", "sponsor_b"),
    ]:
        pid = uuid.UUID(ids[pid_key])
        sid = uuid.UUID(ids[sid_key])
        with sponsor_bootstrap_session(str(sid)) as session:
            indexes = sorted(
                session.scalars(
                    select(Engagement.visit_index).where(
                        Engagement.participant_id == pid
                    )
                ).all()
            )
        assert indexes == list(range(len(indexes))), (
            f"{pid_key}: visit_index not contiguous 0..{len(indexes) - 1}: {indexes}"
        )


def test_all_seeded_engagement_is_synthetic(migrated_db: dict[str, str]) -> None:
    """Every seeded engagement row has synthetic=True."""
    from sqlalchemy import select

    from vigil.db.models import Engagement
    from vigil.repositories.session import sponsor_bootstrap_session

    ids = migrated_db
    for pid_key, sid_key in [
        ("participant_a", "sponsor_a"),
        ("participant_b", "sponsor_b"),
    ]:
        pid = uuid.UUID(ids[pid_key])
        sid = uuid.UUID(ids[sid_key])
        with sponsor_bootstrap_session(str(sid)) as session:
            non_synthetic = session.scalars(
                select(Engagement).where(
                    Engagement.participant_id == pid,
                    Engagement.synthetic == False,  # noqa: E712
                )
            ).all()
        assert non_synthetic == [], (
            f"{pid_key}: {len(non_synthetic)} engagement rows have synthetic=False"
        )


def test_seed_bridge_idempotent(migrated_db: dict[str, str]) -> None:
    """Re-running _seed_engagement for an already-seeded participant inserts 0 new rows."""
    from sqlalchemy import func, select

    from vigil.db.models import Engagement
    from vigil.repositories.session import sponsor_bootstrap_session
    from vigil.seed import _SYNTH_DIR, _map_synthetic_pid, _seed_engagement

    ids = migrated_db
    pid = uuid.UUID(ids["participant_a"])
    sid = uuid.UUID(ids["sponsor_a"])

    par_df = pd.read_parquet(_SYNTH_DIR / "participants.parquet")
    n_synth = len(par_df)
    synthetic_pid = _map_synthetic_pid(pid, n_synth)
    eng_df = pd.read_parquet(
        _SYNTH_DIR / "engagement.parquet",
        filters=[("participant_id", "=", synthetic_pid)],
    )
    par_df = par_df.loc[par_df["participant_id"] == synthetic_pid].reset_index(
        drop=True
    )

    with sponsor_bootstrap_session(str(sid)) as session:
        before = session.scalar(
            select(func.count())
            .select_from(Engagement)
            .where(Engagement.participant_id == pid)
        )

    n_inserted = _seed_engagement(
        sponsor_id=sid,
        participant_id=pid,
        trial_id=uuid.UUID(ids["trial_a"]),
        site_id=uuid.UUID(ids["site_a"]),
        synthetic_pid=synthetic_pid,
        eng_df=eng_df,
        par_df=par_df,
    )
    assert n_inserted == 0, (
        f"idempotency failed: inserted {n_inserted} rows on second call"
    )

    with sponsor_bootstrap_session(str(sid)) as session:
        after = session.scalar(
            select(func.count())
            .select_from(Engagement)
            .where(Engagement.participant_id == pid)
        )
    assert after == before, f"row count changed on second call: {before} → {after}"


def test_engagement_sponsor_id_matches_participant_sponsor(
    migrated_db: dict[str, str],
) -> None:
    """Each seeded engagement row's sponsor_id equals the demo participant's own sponsor."""
    from sqlalchemy import select

    from vigil.db.models import Engagement
    from vigil.repositories.session import sponsor_bootstrap_session

    ids = migrated_db
    for pid_key, sid_key in [
        ("participant_a", "sponsor_a"),
        ("participant_b", "sponsor_b"),
    ]:
        pid = uuid.UUID(ids[pid_key])
        expected_sid = uuid.UUID(ids[sid_key])
        with sponsor_bootstrap_session(str(expected_sid)) as session:
            rows = session.scalars(
                select(Engagement).where(Engagement.participant_id == pid)
            ).all()
        assert len(rows) > 0, f"{pid_key}: no engagement rows to check"
        wrong = [r for r in rows if r.sponsor_id != expected_sid]
        assert wrong == [], f"{pid_key}: {len(wrong)} rows have wrong sponsor_id"


def test_seeded_engagement_invisible_cross_tenant(migrated_db: dict[str, str]) -> None:
    """Sponsor B session sees zero engagement rows for Sponsor A's seeded participant (inv 6)."""
    from sqlalchemy import select

    from vigil.db.models import Engagement
    from vigil.repositories import users as user_repo
    from vigil.repositories.session import auth_lookup_session, scoped_session
    from vigil.services.scope_resolver import resolve_scope

    ids = migrated_db
    pid_a = uuid.UUID(ids["participant_a"])

    with auth_lookup_session() as session:
        user_b = user_repo.get_by_email(session, "coord.b@vigil.example")
        assert user_b is not None
        scope_b = resolve_scope(session, user_b)

    with scoped_session(scope_b) as session:
        rows = session.scalars(
            select(Engagement).where(Engagement.participant_id == pid_a)
        ).all()
    assert rows == [], (
        f"Sponsor A seeded engagement leaked to Sponsor B: {len(rows)} rows visible"
    )


def test_demo_participant_covariates_set_with_honest_provenance(
    migrated_db: dict[str, str],
) -> None:
    """After seed, demo participant A has clinical covariates and honest *_baseline_imputed flags."""
    from vigil.db.models import Participant
    from vigil.repositories.session import sponsor_bootstrap_session

    ids = migrated_db
    pid = uuid.UUID(ids["participant_a"])
    sid = uuid.UUID(ids["sponsor_a"])

    with sponsor_bootstrap_session(str(sid)) as session:
        p = session.get(Participant, pid)

    assert p is not None
    assert p.age_years is not None, "age_years not set after seed bridge"
    assert p.age_years_baseline_imputed is not None, (
        "age_years_baseline_imputed not set"
    )
    assert p.hba1c_pct is not None, "hba1c_pct not set after seed bridge"
    assert p.hba1c_pct_baseline_imputed is not None, (
        "hba1c_pct_baseline_imputed not set"
    )
    assert p.bmi is not None, "bmi not set after seed bridge"
    assert p.bmi_baseline_imputed is not None, "bmi_baseline_imputed not set"
