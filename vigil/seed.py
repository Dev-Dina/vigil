"""Seed for demonstrable isolation (domain.md entities; CLAUDE.md sacred-test fixture).

Creates:
- Sponsor A and Sponsor B (tenant roots), each with one trial, one site.
- One coordinator per sponsor, scoped to that sponsor's site/trial.
- One PI on Sponsor A's site/trial (site role).
- One CRO with one study manager staffed on Sponsor A ONLY (single assignment_grant)
  and one CRA/monitor narrowed to a specific site within Sponsor A's trial.
- One platform admin and one auditor.
- One participant per sponsor (leak is visible: A's coordinator / CRO user see only A;
  nobody crosses the sponsor wall).

All writes go through repositories on a platform-privileged session (bootstrap only).
Run: uv run python -m vigil.seed
Returns the created ids (printed as JSON) so tests can pin the fixture.

NOTE: passwords are demo fixtures, sourced from env (SEED_PASSWORD) with a documented
local default — never a production secret, never logged.
"""

from __future__ import annotations

import hashlib
import json
import os
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy import select

from vigil.core.logging import configure_logging, get_logger
from vigil.core.security import hash_password
from vigil.db.models import (
    AssignmentGrant,
    Engagement,
    Participant,
    RoutingState,
    Site,
    Trial,
    User,
)
from vigil.domain import Role
from vigil.repositories import scoring as scoring_repo
from vigil.repositories import tenancy as tenancy_repo
from vigil.repositories.session import platform_session, sponsor_bootstrap_session

log = get_logger("vigil.seed")

# Demo password for every seeded user. Local-dev default; override via env.
SEED_PASSWORD = os.environ.get("VIGIL_SEED_PASSWORD", "vigil-dev-password")

# Path to the synthetic T2D parquet files (build-time ingestion output, never live-fetched).
_SYNTH_DIR: Path = Path(__file__).parent.parent / "data" / "synthetic" / "t2d"
# Synthetic visit timestamps are anchored to wall-clock now() (not a fixed calendar epoch):
# the operational DB must never hold a visit dated in the FUTURE — the feature-time leakage
# guard (models/leakage_check.assert_feature_time_before_t) correctly rejects any visit at or
# after the decision time (now()). A fixed epoch drifts past now() for longer trajectories as
# time passes; anchoring each trajectory so its LAST visit lands this margin (days) before now()
# keeps every seeded visit strictly in the past, while a uniform shift preserves every
# inter-visit gap (the relative structure the sequence model consumes).
_SEED_FUTURE_MARGIN_DAYS = 1


def _map_synthetic_pid(demo_uuid: uuid.UUID, n_participants: int) -> int:
    """Deterministic mapping: demo participant UUID → synthetic parquet participant index.

    Rule: little-endian uint32 of MD5(uuid.bytes + b"seed-bridge-v1") mod n_participants.
    "seed-bridge-v1" is a versioned salt — change it only to intentionally rotate the mapping.
    The same demo UUID always maps to the same synthetic trajectory regardless of parquet sort order.
    """
    digest = hashlib.md5(demo_uuid.bytes + b"seed-bridge-v1").digest()
    return int.from_bytes(digest[:4], "little") % n_participants


def _seed_engagement(
    *,
    sponsor_id: uuid.UUID,
    participant_id: uuid.UUID,
    trial_id: uuid.UUID,
    site_id: uuid.UUID,
    synthetic_pid: int,
    eng_df: pd.DataFrame,
    par_df: pd.DataFrame,
) -> int:
    """Copy a synthetic trajectory to a demo participant and set their clinical covariates.

    Returns the number of engagement rows inserted (0 if all already exist — idempotent).
    synthetic=True on every row (non-negotiable: specs/scoring.md § synthetic propagation).
    Timestamps: anchored so the LAST visit is _SEED_FUTURE_MARGIN_DAYS before now(), with
    enrollment_day stagger + visit_index * spacing preserving the trajectory's relative shape
    (so no seeded visit is future-dated relative to the scoring decision time).
    miss_probability is intentionally excluded (latent hazard; specs/scoring.md § inv 4).
    *_baseline_imputed flags are read directly from the parquet — provenance is honest.
    """
    par_row = par_df.loc[par_df["participant_id"] == synthetic_pid].iloc[0]
    traj = eng_df.loc[eng_df["participant_id"] == synthetic_pid].sort_values(
        "visit_index"
    )

    n_visits = len(traj)
    enrollment_day = int(par_row["enrollment_day"])
    planned_duration_days = int(par_row["planned_duration_days"])
    spacing_days = planned_duration_days / max(n_visits - 1, 1)

    # Anchor the trajectory to wall-clock now() so its last visit lands a safe margin in the
    # past (see _SEED_FUTURE_MARGIN_DAYS). last-visit offset from the anchor is
    # enrollment_day + max_visit_index * spacing; setting anchor = now_floor - that offset -
    # margin puts the last visit at now_floor - margin. now() is floored to the day so a
    # single seed run is internally consistent.
    max_visit_index = int(traj["visit_index"].max()) if n_visits else 0
    last_visit_offset_days = enrollment_day + max_visit_index * spacing_days
    now_floor = datetime.now(tz=timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    )
    reference_epoch = now_floor - timedelta(
        days=last_visit_offset_days + _SEED_FUTURE_MARGIN_DAYS
    )
    enrollment_date = reference_epoch + timedelta(days=enrollment_day)

    eng_rows = [
        Engagement(
            id=uuid.uuid4(),
            sponsor_id=sponsor_id,
            participant_id=participant_id,
            trial_id=trial_id,
            site_id=site_id,
            visit_index=int(row["visit_index"]),
            visit_timestamp=enrollment_date
            + timedelta(days=int(row["visit_index"]) * spacing_days),
            attended=bool(row["attended"]),
            missed=bool(row["missed"]),
            cumulative_missed=int(row["cumulative_missed"]),
            consecutive_missed=int(row["consecutive_missed"]),
            synthetic=True,
        )
        for _, row in traj.iterrows()
    ]

    with sponsor_bootstrap_session(str(sponsor_id)) as session:
        # Update Trial with static context features from the mapped parquet participant.
        # planned_duration_days is trial-level (spec B2a-spec-2 decision c) — set here, not on p.
        t = session.get(Trial, trial_id)
        assert t is not None, f"demo trial {trial_id} not found in seed"
        t.n_sites = int(par_row["n_sites"])
        t.planned_duration_days = planned_duration_days
        t.phase = str(par_row["phase"]) if pd.notna(par_row["phase"]) else None
        session.flush()

        p = session.get(Participant, participant_id)
        assert p is not None, f"demo participant {participant_id} not found in seed"
        p.age_years = (
            float(par_row["age_years"]) if pd.notna(par_row["age_years"]) else None
        )
        p.age_years_baseline_imputed = bool(par_row["age_baseline_imputed"])
        p.hba1c_pct = (
            float(par_row["hba1c_pct"]) if pd.notna(par_row["hba1c_pct"]) else None
        )
        p.hba1c_pct_baseline_imputed = bool(par_row["hba1c_baseline_imputed"])
        p.bmi = float(par_row["bmi"]) if pd.notna(par_row["bmi"]) else None
        p.bmi_baseline_imputed = bool(par_row["bmi_baseline_imputed"])
        p.sex = str(par_row["sex"]) if pd.notna(par_row["sex"]) else None
        p.arm_type = str(par_row["arm_type"]) if pd.notna(par_row["arm_type"]) else None
        session.flush()

        existing_indexes: set[int] = set(
            session.scalars(
                select(Engagement.visit_index).where(
                    Engagement.participant_id == participant_id
                )
            ).all()
        )
        new_rows = [r for r in eng_rows if r.visit_index not in existing_indexes]
        for r in new_rows:
            session.add(r)
        session.flush()

    return len(new_rows)


def _add_user(session, *, email, role, **kw) -> User:  # type: ignore[no-untyped-def]
    user = User(
        email=email,
        password_hash=hash_password(SEED_PASSWORD),
        role=str(role),
        **kw,
    )
    session.add(user)
    session.flush()
    return user


def _seed_sponsor_tenant_rows(
    sponsor_id: uuid.UUID, prefix: str
) -> dict[str, uuid.UUID]:
    """Insert one trial/site/participant under a sponsor-bound session.

    RLS binds to that sponsor. UUIDs returned so the platform pass can FK to them.
    """
    trial_id = uuid.uuid4()
    site_id = uuid.uuid4()
    participant_id = uuid.uuid4()
    risk = (0.82, "high") if prefix == "A" else (0.40, "medium")
    with sponsor_bootstrap_session(str(sponsor_id)) as session:
        session.add(Trial(id=trial_id, sponsor_id=sponsor_id, name=f"Trial {prefix}1"))
        session.flush()
        session.add(
            Site(
                id=site_id,
                sponsor_id=sponsor_id,
                trial_id=trial_id,
                name=f"Site {prefix}1",
            )
        )
        session.flush()
        session.add(
            Participant(
                id=participant_id,
                sponsor_id=sponsor_id,
                trial_id=trial_id,
                site_id=site_id,
                coded_ref=f"{prefix}-0001",
                risk_score=risk[0],
                risk_band=risk[1],
            )
        )
        session.flush()
    return {"trial": trial_id, "site": site_id, "participant": participant_id}


def seed() -> dict[str, str]:
    configure_logging("INFO")
    ids: dict[str, str] = {}

    # 1) Tenant roots + global CRO registry (platform-privileged bootstrap).
    with platform_session() as session:
        cro = tenancy_repo.create_cro(session, name="Acme CRO")
        sponsor_a = tenancy_repo.create_sponsor(session, name="Sponsor A")
        sponsor_b = tenancy_repo.create_sponsor(session, name="Sponsor B")
        cro_id, sponsor_a_id, sponsor_b_id = cro.id, sponsor_a.id, sponsor_b.id
    ids.update(
        cro_id=str(cro_id), sponsor_a=str(sponsor_a_id), sponsor_b=str(sponsor_b_id)
    )

    # 2) Each sponsor's operational rows, inserted bound to that sponsor (RLS enforced).
    tenant_a = _seed_sponsor_tenant_rows(sponsor_a_id, "A")
    tenant_b = _seed_sponsor_tenant_rows(sponsor_b_id, "B")
    ids.update(
        trial_a=str(tenant_a["trial"]),
        site_a=str(tenant_a["site"]),
        participant_a=str(tenant_a["participant"]),
        trial_b=str(tenant_b["trial"]),
        site_b=str(tenant_b["site"]),
        participant_b=str(tenant_b["participant"]),
    )

    # 3) Users + single CRO grant (cross-tenant by design → platform bootstrap session).
    with platform_session() as session:
        admin = _add_user(
            session, email="admin@vigil.example", role=Role.PLATFORM_ADMIN
        )
        auditor = _add_user(session, email="auditor@vigil.example", role=Role.AUDITOR)
        ids.update(platform_admin=str(admin.id), auditor=str(auditor.id))

        oversight_a = _add_user(
            session,
            email="oversight.a@vigil.example",
            role=Role.SPONSOR_OVERSIGHT,
            home_sponsor_id=sponsor_a_id,
        )
        oversight_b = _add_user(
            session,
            email="oversight.b@vigil.example",
            role=Role.SPONSOR_OVERSIGHT,
            home_sponsor_id=sponsor_b_id,
        )
        ids.update(oversight_a=str(oversight_a.id), oversight_b=str(oversight_b.id))

        coord_a = _add_user(
            session,
            email="coord.a@vigil.example",
            role=Role.COORDINATOR,
            home_sponsor_id=sponsor_a_id,
            home_trial_id=tenant_a["trial"],
            home_site_id=tenant_a["site"],
        )
        coord_b = _add_user(
            session,
            email="coord.b@vigil.example",
            role=Role.COORDINATOR,
            home_sponsor_id=sponsor_b_id,
            home_trial_id=tenant_b["trial"],
            home_site_id=tenant_b["site"],
        )
        ids.update(coordinator_a=str(coord_a.id), coordinator_b=str(coord_b.id))

        # PI on Sponsor A's site/trial (site role: own site & trial).
        pi_a = _add_user(
            session,
            email="pi.a@vigil.example",
            role=Role.PRINCIPAL_INVESTIGATOR,
            home_sponsor_id=sponsor_a_id,
            home_trial_id=tenant_a["trial"],
            home_site_id=tenant_a["site"],
        )
        ids["pi_a"] = str(pi_a.id)

        # One CRO study manager staffed on Sponsor A ONLY (single grant → cannot see B).
        cro_mgr = _add_user(
            session,
            email="cro.manager@vigil.example",
            role=Role.STUDY_MANAGER,
            home_cro_id=cro_id,
        )
        ids["cro_manager"] = str(cro_mgr.id)
        session.add(
            AssignmentGrant(
                user_id=cro_mgr.id,
                sponsor_id=sponsor_a_id,  # A only — no grant for B
                trial_id=None,
                site_id=None,
                granted_by=admin.id,
            )
        )
        session.flush()

        # One CRO CRA/monitor staffed via a grant narrowed to a specific site within
        # Sponsor A's trial (site-level CRO scope; no home sponsor, home_cro_id set).
        cra = _add_user(
            session,
            email="cra@vigil.example",
            role=Role.CRA,
            home_cro_id=cro_id,
        )
        ids["cra"] = str(cra.id)
        session.add(
            AssignmentGrant(
                user_id=cra.id,
                sponsor_id=sponsor_a_id,  # A only
                trial_id=tenant_a["trial"],  # narrowed to A's trial
                site_id=tenant_a["site"],  # narrowed to A's site
                granted_by=admin.id,
            )
        )
        session.flush()

    # 4) Scoring demo rows: one ParticipantScore per sponsor.
    #    Written under sponsor-bound session so RLS is enforced on insert.
    _seed_score(sponsor_a_id, tenant_a, ids, "score_a")
    _seed_score(sponsor_b_id, tenant_b, ids, "score_b")

    # 5) Seed bridge: copy synthetic T2D engagement trajectories to demo participants.
    #    Mapping rule: synthetic_pid = uint32(MD5(uuid.bytes + b"seed-bridge-v1")[:4]) % n.
    #    Timestamps anchored so the last visit precedes now() (no future-dated visits);
    #    miss_probability withheld (latent hazard, inv 4).
    #    Each demo UUID maps to exactly one synthetic trajectory; same UUID → same result always.
    eng_df = pd.read_parquet(_SYNTH_DIR / "engagement.parquet")
    par_df = pd.read_parquet(_SYNTH_DIR / "participants.parquet")
    n_synth = len(par_df)
    for demo_pid_str, _sponsor_id, _tenant in [
        (ids["participant_a"], sponsor_a_id, tenant_a),
        (ids["participant_b"], sponsor_b_id, tenant_b),
    ]:
        demo_uuid = uuid.UUID(demo_pid_str)
        synthetic_pid = _map_synthetic_pid(demo_uuid, n_synth)
        n_inserted = _seed_engagement(
            sponsor_id=_sponsor_id,
            participant_id=demo_uuid,
            trial_id=_tenant["trial"],
            site_id=_tenant["site"],
            synthetic_pid=synthetic_pid,
            eng_df=eng_df,
            par_df=par_df,
        )
        log.info(
            "seed.engagement",
            extra={
                "extra": {
                    "participant": demo_pid_str,
                    "synthetic_pid": synthetic_pid,
                    "inserted": n_inserted,
                }
            },
        )
    ids.update(
        seed_bridge_a_synthetic_pid=str(
            _map_synthetic_pid(uuid.UUID(ids["participant_a"]), n_synth)
        ),
        seed_bridge_b_synthetic_pid=str(
            _map_synthetic_pid(uuid.UUID(ids["participant_b"]), n_synth)
        ),
    )

    # 6) Routing state: champion + shadow rows for the demo regime (t2d).
    #    Champion: sequence LSTM (B2b); shadow: structural GBT (B2c).
    #    UNIQUE(regime, role) — one champion + one shadow per regime at a time.
    with platform_session() as session:
        session.add(
            RoutingState(
                regime="t2d",
                role="champion",
                model_version="sequence_v1.0:demo",
                model_card_ref="data/models/t2d/model_card.md",
            )
        )
        session.add(
            RoutingState(
                regime="t2d",
                role="shadow",
                model_version="structural_v1.0:t2d",
                model_card_ref="data/models/t2d/model_card_structural.md",
            )
        )
        session.flush()
    ids["routing_t2d_champion"] = "sequence_v1.0:demo"
    ids["routing_t2d_shadow"] = "structural_v1.0:t2d"

    log.info("seed.complete", extra={"extra": {"entities": len(ids)}})
    return ids


def _seed_score(
    sponsor_id: uuid.UUID,
    tenant: dict[str, uuid.UUID],
    ids: dict[str, str],
    key: str,
) -> None:
    """Write a demo ParticipantScore row under the sponsor-bound session."""
    with sponsor_bootstrap_session(str(sponsor_id)) as session:
        row = scoring_repo.upsert_score(
            session,
            participant_id=tenant["participant"],
            sponsor_id=sponsor_id,
            trial_id=tenant["trial"],
            site_id=tenant["site"],
            risk_score=0.82 if key == "score_a" else 0.40,
            risk_band="high" if key == "score_a" else "medium",
            top_factors=["missed_visits", "hba1c_trend", "bmi"]
            if key == "score_a"
            else ["missed_calls"],
            reasons=[{"factor": "missed_visits", "contribution": 0.45}]
            if key == "score_a"
            else [{"factor": "missed_calls", "contribution": 0.20}],
            model_version="sequence_v1.0:demo",
            model_card_ref="data/models/t2d/model_card.md",
            synthetic=True,
        )
        scoring_repo.write_score_audit(
            session,
            sponsor_id=sponsor_id,
            participant_id=tenant["participant"],
            model_version="sequence_v1.0:demo",
            synthetic=True,
        )
    ids[key] = str(row.id)


def main() -> None:
    ids = seed()
    print(json.dumps(ids, indent=2))


if __name__ == "__main__":
    main()
