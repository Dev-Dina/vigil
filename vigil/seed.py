"""Seed for demonstrable isolation (domain.md entities; CLAUDE.md sacred-test fixture).

Creates (Gate SEED-DEMO — a demo-worthy multi-trial cohort):
- Sponsor A and Sponsor B (tenant roots), each with TWO trials, a site each (4 trials total).
- ~9 participants per trial (~36 total), each bridged to a synthetic trajectory ("low") or given a
  PLANTED accruing missed-visit sequence ("medium"/"high") — the band SPREAD is produced by the REAL
  champion at scoring (PREP step), never hand-set. The trial-1 / first participant of each sponsor is
  the canonical fixture (A-0001 / B-0001) the spine tests pin.
- One coordinator per sponsor, scoped to that sponsor's trial-1 site (sees their site's slice); the
  per-sponsor SPONSOR_OVERSIGHT (oversight.a / oversight.b) sees that sponsor's FULL multi-trial cohort.
- One PI on Sponsor A's site/trial (site role).
- One CRO with one study manager staffed on Sponsor A ONLY (single assignment_grant)
  and one CRA/monitor narrowed to a specific site within Sponsor A's trial.
- One platform admin and one auditor; the MLOps + LLMOps platform-tier ops roles.
- t2d routing: champion (sequence_v1.1:demo) + shadow (structural_v1.0:t2d) + challenger
  (sequence_v1.2:demo), mirrored into the model_registry catalog (the full governance story).
- Cross-tenant leak stays visible: A's coordinator / CRO user see only A; nobody crosses the wall.

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
import sys
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
from sqlalchemy import delete, func, select
from sqlalchemy.orm import Session

from vigil.core.logging import configure_logging, get_logger
from vigil.core.security import hash_password
from vigil.db.models import (
    AssignmentGrant,
    Cro,
    Engagement,
    ModelRegistry,
    Participant,
    RoutingState,
    Site,
    Sponsor,
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
# Demo serious-risk doorbell recipient (Gate 9.5) — supplied at SEED TIME via the environment,
# NEVER a committed literal (domain.md § Notification routing). None → coord.a is seeded with no
# notification email (the field defaults UNSET for everyone).
_DEMO_NOTIFY_EMAIL = os.environ.get("VIGIL_DEMO_NOTIFY_EMAIL") or None
# Demo drift-breach ALERT recipient (Gate M2) — the platform/ML engineer's address, supplied at
# SEED TIME via the environment, NEVER a committed literal (same posture as VIGIL_DEMO_NOTIFY_EMAIL).
# None → the platform_admin is seeded with no notification email (defaults UNSET, like everyone).
_DEMO_ML_NOTIFY_EMAIL = os.environ.get("VIGIL_DEMO_ML_NOTIFY_EMAIL") or None
# Gate DRIFT-EMAIL-FIX: so a FRESH seed yields a working drift-alert recipient WITHOUT requiring the
# env var, the mlops_engineer (RBAC-OPS — the role that OWNS the model lifecycle: drift-run/register/
# promote) is seeded with a notification_email that DEFAULTS to its OWN demo login address
# (mlops@vigil.example, an RFC-2606 example domain — NOT a real inbox; Mailpit catches it regardless).
# Set VIGIL_DEMO_ML_NOTIFY_EMAIL to route to a real demo inbox instead. This is a targeted demo
# recipient, NOT a blanket default (every other seeded user still defaults UNSET).
_DEMO_ML_DEFAULT_NOTIFY = (
    os.environ.get("VIGIL_DEMO_ML_NOTIFY_EMAIL") or "mlops@vigil.example"
)

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

# SEED-GUARD: stable natural keys that identify a PRIOR demo seed. UUIDs are random per run, but
# these are constant — the sponsor names are committed in the seed's very first step (so even a
# partially-crashed prior run is detected), and every seeded user lives at the RFC-2606 example
# domain @vigil.example (the drift-proof selector the --force wipe deletes by).
_SEED_SPONSOR_NAMES: tuple[str, ...] = ("Sponsor A", "Sponsor B")
_SEED_USER_EMAIL_LIKE = "%@vigil.example"


class SeedExistsError(RuntimeError):
    """Raised when :func:`seed` runs against a DB that already holds the demo seed (no ``force``).

    The seed APPENDS with fresh UUIDs every run, so a second unguarded run doubles the cohort. This
    turns that accident into a clean, explicit refusal. ``n_participants`` is the live count for the
    operator message.
    """

    def __init__(self, n_participants: int) -> None:
        self.n_participants = n_participants
        super().__init__(f"DB already seeded ({n_participants} participants present)")


def _existing_seed_sponsors(session: Session) -> list[Sponsor]:
    """The demo seed sponsors present in the DB — the stable 'already seeded' signal (RLS-readable
    under the platform session via the sponsor_self ``is_platform`` clause)."""
    return list(
        session.execute(
            select(Sponsor).where(Sponsor.name.in_(_SEED_SPONSOR_NAMES))
        ).scalars()
    )


def _count_seeded_participants(sponsor_ids: list[uuid.UUID]) -> int:
    """Total participants across the existing seed sponsors. Participants are sponsor-RLS'd (NOT
    visible under the platform session), so the count is taken under each sponsor's bound session."""
    total = 0
    for sid in sponsor_ids:
        with sponsor_bootstrap_session(str(sid)) as session:
            total += int(
                session.execute(select(func.count()).select_from(Participant)).scalar()
                or 0
            )
    return total


def _wipe_demo_data(sponsor_ids: list[uuid.UUID]) -> None:
    """Delete the prior demo seed so a ``--force`` reseed lands a clean cohort (36, not 72).

    FK-safe deletion order (RESTRICT edges force this sequence):
      1. participants — under each sponsor's RLS session; cascades engagement/participant_score/
         risk_crossing/intervention (ON DELETE CASCADE). This also clears the intervention→user
         RESTRICT edge so the users can be removed next.
      2. assignment_grants then @vigil.example users — under the platform session. Grants go first
         (the users' ``granted_by`` is a RESTRICT self-edge). Users carry ``home_site_id``/
         ``home_trial_id``/``home_sponsor_id`` RESTRICT edges, so they MUST be gone before sites/
         trials/sponsors.
      3. sites then trials — under each sponsor's RLS session (now unreferenced by any user).
      4. the t2d routing/registry projection, the sponsors, and the demo CRO — platform session.
    ``audit_log`` history is intentionally retained (append-only; its FKs are SET NULL on delete).
    Runs as the app role: DELETE (RLS-scoped), never TRUNCATE (needs no owner/BYPASSRLS privilege).
    """
    # 1) Participants first (cascades engagement/score/crossing/intervention → clears the
    #    intervention→user RESTRICT edge).
    for sid in sponsor_ids:
        with sponsor_bootstrap_session(str(sid)) as session:
            session.execute(delete(Participant))
    # 2) Grants then users (users RESTRICT-reference site/trial/sponsor via home_*, so they precede
    #    those deletes; grants precede users for the granted_by RESTRICT self-edge).
    with platform_session() as session:
        session.execute(
            delete(AssignmentGrant).where(AssignmentGrant.sponsor_id.in_(sponsor_ids))
        )
        session.execute(delete(User).where(User.email.like(_SEED_USER_EMAIL_LIKE)))
    # 3) Sites then trials (now unreferenced by any user.home_* edge).
    for sid in sponsor_ids:
        with sponsor_bootstrap_session(str(sid)) as session:
            session.execute(delete(Site))
            session.execute(delete(Trial))
    # 4) Routing/registry projection, sponsors, CRO.
    with platform_session() as session:
        session.execute(delete(RoutingState).where(RoutingState.regime == "t2d"))
        session.execute(delete(ModelRegistry).where(ModelRegistry.regime == "t2d"))
        session.execute(delete(Sponsor).where(Sponsor.id.in_(sponsor_ids)))
        session.execute(delete(Cro).where(Cro.name == "Acme CRO"))


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
        # FIX-5: anchor enrolled_at to the trajectory START (== visit_index 0 timestamp), not the
        # DB default now(). Without this, enrolled_at defaults to the seed-run moment, so the
        # detail page's "Days Enrolled" reads ~0 even though the visit history spans months.
        p.enrolled_at = enrollment_date
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


# --- Demo cohort shape (Gate SEED-DEMO) --------------------------------------------------------
# Each sponsor gets 2 trials; each trial _PARTICIPANTS_PER_TRIAL participants. The band SPREAD is
# produced by the REAL champion (NEVER hand-set): a "low" participant is bridged to a synthetic
# trajectory (the cohort is mostly low-dropout), while "medium"/"high" carry a PLANTED accruing
# missed-visit sequence (the Gate 9.7 demo-loop signal) the real calibrated LSTM scores up. Tuned so
# each sponsor lands ~3 high + a few medium + mostly low, and each coordinator's HOME trial (trial 1)
# carries exactly ONE high — a small, legible at-risk set. Every row stays SYNTHETIC-labeled +
# coded-ref only (no names/PII). Scoring is a PREP step (trigger score_trial per trial) — the seed
# stays hermetic (no torch) so the spine can seed it every session.
_PARTICIPANTS_PER_TRIAL = 9
_TRIAL1_PROFILES = (
    "low",
    "low",
    "medium",
    "low",
    "high",
    "low",
    "medium",
    "low",
    "low",
)
_TRIAL2_PROFILES = (
    "low",
    "high",
    "low",
    "medium",
    "low",
    "high",
    "low",
    "medium",
    "low",
)
# Planted accruing missed-visit sequences (2 attended → then consecutive misses). Under the real
# calibrated champion: ~5 consecutive → medium (>0.3; a couple cross 0.6 under some trial contexts),
# ~12 consecutive → high (>0.6). NOT a hand-set band — the pattern drives the model; the model
# assigns the band (thresholds >0.6 high, >0.3 med).
_PLANT_PATTERN: dict[str, list[bool]] = {
    "medium": [False, False] + [True] * 5,
    "high": [False, False] + [True] * 12,
}
_PLANT_SPACING_DAYS = 28  # ~monthly visits


def _seed_planted_engagement(
    *,
    sponsor_id: uuid.UUID,
    participant_id: uuid.UUID,
    trial_id: uuid.UUID,
    site_id: uuid.UUID,
    profile: str,
    idx: int,
) -> None:
    """Plant a synthetic accruing missed-visit trajectory + demo-shape covariates for a demo
    participant so the REAL champion scores it up. Mirrors scripts.demo_clinical_ops_loop: a
    literature-shaped PLANTED signal — every engagement row synthetic=True, NO hand-set band.

    enrolled_at is anchored to the trajectory START (FIX-5), varied per ``idx`` so days-enrolled
    differs, and placed far enough in the past that the LAST visit stays strictly before now() (the
    feature-time leakage guard rejects any visit at/after the decision time).
    """
    pattern = _PLANT_PATTERN[profile]
    # base_days_ago > span (= spacing * (len-1)); high span = 28*13 = 364, so 400+ keeps every
    # visit in the past. The per-idx stagger spreads enrolled_at across the cohort.
    base_days_ago = 400 + idx * 13
    base = datetime.now(tz=timezone.utc).replace(
        hour=0, minute=0, second=0, microsecond=0
    ) - timedelta(days=base_days_ago)

    cum = cons = 0
    eng_rows: list[dict] = []
    for vi, missed in enumerate(pattern):
        if missed:
            cum += 1
            cons += 1
        else:
            cons = 0
        eng_rows.append(
            {
                "visit_index": vi,
                "visit_timestamp": base + timedelta(days=_PLANT_SPACING_DAYS * vi),
                "attended": not missed,
                "missed": missed,
                "cumulative_missed": cum,
                "consecutive_missed": cons,
            }
        )

    with sponsor_bootstrap_session(str(sponsor_id)) as session:
        p = session.get(Participant, participant_id)
        assert p is not None, f"planted participant {participant_id} not found in seed"
        # FIX-5: enrolled_at == trajectory start (not the seed-run moment). Demo-shape covariates,
        # varied per idx so the cohort is not a single repeated row; all clearly synthetic.
        p.enrolled_at = base
        p.age_years = 55.0 + (idx * 3) % 30
        p.hba1c_pct = round(6.8 + ((idx * 7) % 10) * 0.2, 1)
        p.bmi = 25.0 + (idx * 5) % 12
        p.sex = "Female" if idx % 2 else "Male"
        p.arm_type = "Placebo Comparator" if idx % 2 else "Active Comparator"
        session.flush()
        for r in eng_rows:
            session.add(
                Engagement(
                    id=uuid.uuid4(),
                    sponsor_id=sponsor_id,
                    participant_id=participant_id,
                    trial_id=trial_id,
                    site_id=site_id,
                    synthetic=True,
                    **r,
                )
            )
        session.flush()


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


def _build_sponsor_cohort(sponsor_id: uuid.UUID, prefix: str) -> dict:
    """Insert TWO trials (each with a site) + a spread of participants under a sponsor-bound session.

    RLS binds to that sponsor. Participants are bare rows (coded-ref only, NO hand-set band — the
    denormalized risk columns default low/0.0 and are filled by the REAL champion at scoring). Each
    descriptor carries its trajectory ``profile`` (low → synthetic-bridge; medium/high → planted) so
    the engagement pass can drive an honest, model-produced band spread. The trial-1 / first
    participant is the canonical fixture (``{prefix}-0001``) the spine tests pin.
    """
    out: dict = {"participants": []}
    seq = 0
    with sponsor_bootstrap_session(str(sponsor_id)) as session:
        for ti, (label, profiles) in enumerate(
            (("1", _TRIAL1_PROFILES), ("2", _TRIAL2_PROFILES)), start=1
        ):
            trial_id = uuid.uuid4()
            site_id = uuid.uuid4()
            session.add(
                Trial(
                    id=trial_id,
                    sponsor_id=sponsor_id,
                    # Honest regime hint: the champion is a t2d-regime dropout model. Coded/synthetic,
                    # no invented sponsor brand; sites stay coded (Site A1, ...).
                    name=f"T2D Retention - Trial {prefix}{label}",
                )
            )
            session.flush()
            session.add(
                Site(
                    id=site_id,
                    sponsor_id=sponsor_id,
                    trial_id=trial_id,
                    name=f"Site {prefix}{label}",
                )
            )
            session.flush()
            out[f"trial{ti}"] = trial_id
            out[f"site{ti}"] = site_id
            for profile in profiles:
                seq += 1
                participant_id = uuid.uuid4()
                session.add(
                    Participant(
                        id=participant_id,
                        sponsor_id=sponsor_id,
                        trial_id=trial_id,
                        site_id=site_id,
                        coded_ref=f"{prefix}-{seq:04d}",
                        status="active",
                    )
                )
                session.flush()
                out["participants"].append(
                    {
                        "id": participant_id,
                        "trial": trial_id,
                        "site": site_id,
                        "profile": profile,
                        "coded_ref": f"{prefix}-{seq:04d}",
                        "idx": seq,
                    }
                )
    return out


def seed(*, force: bool = False) -> dict[str, str]:
    """Seed the demo cohort. Idempotent-by-guard: refuses to run on an ALREADY-seeded DB (the seed
    APPENDS with fresh UUIDs, so a second unguarded run doubles the cohort). ``force=True`` first
    wipes the prior demo data, then reseeds. A clean/empty DB always seeds normally (the tests/CI
    path). Raises :class:`SeedExistsError` when already seeded and ``force`` is False."""
    configure_logging("INFO")

    # SEED-GUARD: detect a prior seed by its stable natural key (the demo sponsor names — committed
    # in step 1 below, so even a partially-crashed prior run is caught). A clean DB has none → seeds
    # normally (the conftest/CI path). With the persistent postgres volume this is the line between
    # an idempotent no-op and silent data bloat (36 → 72 → ...).
    with platform_session() as session:
        existing = _existing_seed_sponsors(session)
    if existing:
        sponsor_ids = [sp.id for sp in existing]
        if not force:
            raise SeedExistsError(_count_seeded_participants(sponsor_ids))
        log.warning(
            "seed.force_reset",
            extra={
                "extra": {"sponsors": len(sponsor_ids), "action": "wipe_and_reseed"}
            },
        )
        _wipe_demo_data(sponsor_ids)

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

    # 2) Each sponsor's operational cohort (2 trials × a spread of participants), bound to that
    #    sponsor (RLS enforced). The trial-1 / first participant stays the canonical fixture the
    #    spine tests pin (participant_a/trial_a/site_a unchanged); trial 2 is the second site.
    cohort_a = _build_sponsor_cohort(sponsor_a_id, "A")
    cohort_b = _build_sponsor_cohort(sponsor_b_id, "B")
    tenant_a = {
        "trial": cohort_a["trial1"],
        "site": cohort_a["site1"],
        "participant": cohort_a["participants"][0]["id"],
    }
    tenant_b = {
        "trial": cohort_b["trial1"],
        "site": cohort_b["site1"],
        "participant": cohort_b["participants"][0]["id"],
    }
    ids.update(
        trial_a=str(tenant_a["trial"]),
        site_a=str(tenant_a["site"]),
        participant_a=str(tenant_a["participant"]),
        trial_b=str(tenant_b["trial"]),
        site_b=str(tenant_b["site"]),
        participant_b=str(tenant_b["participant"]),
        # Second trial/site per sponsor (the multi-trial demo; coord stays on trial 1).
        trial_a2=str(cohort_a["trial2"]),
        site_a2=str(cohort_a["site2"]),
        trial_b2=str(cohort_b["trial2"]),
        site_b2=str(cohort_b["site2"]),
    )

    # 3) Users + single CRO grant (cross-tenant by design → platform bootstrap session).
    with platform_session() as session:
        admin = _add_user(
            session,
            email="admin@vigil.example",
            role=Role.PLATFORM_ADMIN,
            # Gate M2 drift-breach alert recipient — SEED DATA from the environment, NEVER a
            # committed code constant (mirrors coord.a's VIGIL_DEMO_NOTIFY_EMAIL). Set
            # VIGIL_DEMO_ML_NOTIFY_EMAIL at seed time to enable the demo drift alert; unset → the
            # platform_admin has no notification email, like every other seeded user.
            notification_email=_DEMO_ML_NOTIFY_EMAIL,
        )
        auditor = _add_user(session, email="auditor@vigil.example", role=Role.AUDITOR)
        ids.update(platform_admin=str(admin.id), auditor=str(auditor.id))

        # Operational platform-tier roles (Gate RBAC-OPS): action-separated, NO sponsor scope.
        # The MLOps engineer owns the model lifecycle (drift/registry/promote); the LLMOps engineer
        # owns LLM operations (cost/guardrail, read-only). Both are platform-tier like admin/auditor
        # — they reach platform tables only and can NEVER see a tenant row (RLS fails closed).
        mlops = _add_user(
            session,
            email="mlops@vigil.example",
            role=Role.MLOPS_ENGINEER,
            # DRIFT-EMAIL-FIX: the natural drift-alert recipient. Defaults to the demo address above
            # so a fresh seed has a working recipient; VIGIL_DEMO_ML_NOTIFY_EMAIL overrides it.
            notification_email=_DEMO_ML_DEFAULT_NOTIFY,
        )
        llmops = _add_user(
            session, email="llmops@vigil.example", role=Role.LLMOPS_ENGINEER
        )
        ids.update(mlops_engineer=str(mlops.id), llmops_engineer=str(llmops.id))

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
            # Demo serious-risk doorbell recipient — SEED DATA from the environment, NEVER a
            # committed code constant (domain.md § Notification routing). Set
            # VIGIL_DEMO_NOTIFY_EMAIL at seed time (e.g. the demo Gmail) to enable the demo email;
            # unset → coord.a has no notification email, like every other seeded user.
            notification_email=_DEMO_NOTIFY_EMAIL,
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

    # 5) Engagement pass — give EVERY seeded participant a trajectory the REAL model can score:
    #    "low" → bridge to a synthetic T2D trajectory (MD5 map, mostly low-dropout); "medium"/"high"
    #    → a PLANTED accruing missed-visit sequence the real champion scores up. Timestamps anchored
    #    so the last visit precedes now() (no future-dated visits); every row synthetic=True. No
    #    hand-set band — bands appear when score_trial runs at PREP (the seed stays torch-free).
    eng_df = pd.read_parquet(_SYNTH_DIR / "engagement.parquet")
    par_df = pd.read_parquet(_SYNTH_DIR / "participants.parquet")
    n_synth = len(par_df)
    n_low = n_planted = 0
    for cohort, _sponsor_id in ((cohort_a, sponsor_a_id), (cohort_b, sponsor_b_id)):
        for d in cohort["participants"]:
            if d["profile"] == "low":
                synthetic_pid = _map_synthetic_pid(d["id"], n_synth)
                _seed_engagement(
                    sponsor_id=_sponsor_id,
                    participant_id=d["id"],
                    trial_id=d["trial"],
                    site_id=d["site"],
                    synthetic_pid=synthetic_pid,
                    eng_df=eng_df,
                    par_df=par_df,
                )
                n_low += 1
            else:
                _seed_planted_engagement(
                    sponsor_id=_sponsor_id,
                    participant_id=d["id"],
                    trial_id=d["trial"],
                    site_id=d["site"],
                    profile=d["profile"],
                    idx=d["idx"],
                )
                n_planted += 1
    n_participants = len(cohort_a["participants"]) + len(cohort_b["participants"])
    log.info(
        "seed.engagement",
        extra={"extra": {"low_bridged": n_low, "planted": n_planted}},
    )
    ids.update(
        # Retained for compatibility: the canonical participants are "low" (bridged) — their map.
        seed_bridge_a_synthetic_pid=str(
            _map_synthetic_pid(uuid.UUID(ids["participant_a"]), n_synth)
        ),
        seed_bridge_b_synthetic_pid=str(
            _map_synthetic_pid(uuid.UUID(ids["participant_b"]), n_synth)
        ),
        participants_total=str(n_participants),
        trials_total="4",
    )

    # 6) Routing state + registry: the full champion/challenger/shadow governance story for t2d.
    #    routing_state is the live PROJECTION (UNIQUE(regime, role) — one of each role); model_registry
    #    is the durable CATALOG with the SUPPLIED offline metrics + provenance (recorded, never
    #    computed; provenance='architecture_validation' — synthetic method validity, NOT a clinical
    #    claim). The challenger (sequence_v1.2:demo) is registered for the governance demo only; it is
    #    NOT scored (score_trial loads champion + shadow only) and never reaches the champion surface.
    champion_mv, shadow_mv, challenger_mv = (
        "sequence_v1.1:demo",
        "structural_v1.0:t2d",
        "sequence_v1.2:demo",
    )
    champion_card = "data/models/t2d/model_card.md"
    shadow_card = "data/models/t2d/model_card_structural.md"
    with platform_session() as session:
        session.add_all(
            [
                RoutingState(
                    regime="t2d",
                    role="champion",
                    model_version=champion_mv,
                    model_card_ref=champion_card,
                ),
                RoutingState(
                    regime="t2d",
                    role="shadow",
                    model_version=shadow_mv,
                    model_card_ref=shadow_card,
                ),
                RoutingState(
                    regime="t2d",
                    role="challenger",
                    model_version=challenger_mv,
                    model_card_ref=champion_card,
                ),
            ]
        )
        # Catalog rows (Gate M3): SUPPLIED offline metrics from the model cards (champion/shadow) +
        # a clearly-synthetic architecture-validation entry for the demo challenger. All labelled
        # 'architecture_validation' — synthetic method validity, never a clinical claim.
        session.add_all(
            [
                ModelRegistry(
                    regime="t2d",
                    model_version=champion_mv,
                    status="champion",
                    artifact_ref="data/models/t2d/sequence_v1.1_demo.pt",
                    model_card_ref=champion_card,
                    metrics={
                        "pr_auc": 0.339,
                        "brier": 0.07526,
                        "roc_auc": 0.790061,
                        "ece": 0.0075,
                    },
                    provenance="architecture_validation",
                    notes="isotonic-calibrated sequence LSTM; synthetic VAL fold (group-disjoint).",
                ),
                ModelRegistry(
                    regime="t2d",
                    model_version=shadow_mv,
                    status="shadow",
                    artifact_ref="data/models/t2d/structural_v1.0_t2d.pkl",
                    model_card_ref=shadow_card,
                    metrics={"pr_auc": 0.3425, "brier": 0.1258},
                    provenance="architecture_validation",
                    notes="structural GBT shadow; real structural floor (~0.34 PR-AUC) context.",
                ),
                ModelRegistry(
                    regime="t2d",
                    model_version=challenger_mv,
                    status="challenger",
                    artifact_ref="data/models/t2d/sequence_v1.2_demo.pt",
                    model_card_ref=champion_card,
                    metrics={"pr_auc": 0.345, "brier": 0.0747, "roc_auc": 0.792},
                    provenance="architecture_validation",
                    notes=(
                        "DEMO challenger registered to exercise the champion/challenger/shadow "
                        "governance projection; supplied synthetic VAL-fold metrics, awaiting a "
                        "governed promotion. Not a trained production artifact, not a clinical claim."
                    ),
                ),
            ]
        )
        session.flush()
    ids["routing_t2d_champion"] = champion_mv
    ids["routing_t2d_shadow"] = shadow_mv
    ids["routing_t2d_challenger"] = challenger_mv

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
        row = scoring_repo.append_score(
            session,
            participant_id=tenant["participant"],
            sponsor_id=sponsor_id,
            trial_id=tenant["trial"],
            site_id=tenant["site"],
            risk_score=0.82 if key == "score_a" else 0.40,
            risk_band="high" if key == "score_a" else "medium",
            # Gate 9.7: NO fabricated reasons. top_factors/reasons stay EMPTY until the first
            # REAL calibrated-champion score (Gate 9.1 attribution) populates them — a seeded
            # demo row must never surface a hand-authored clinical rationale the model never
            # produced (specs/scoring.md § Phase 9 honesty rule).
            top_factors=[],
            reasons=[],
            model_version="sequence_v1.1:demo",
            model_card_ref="data/models/t2d/model_card.md",
            synthetic=True,
        )
        scoring_repo.write_score_audit(
            session,
            sponsor_id=sponsor_id,
            participant_id=tenant["participant"],
            model_version="sequence_v1.1:demo",
            synthetic=True,
        )
    ids[key] = str(row.id)


def main() -> None:
    # --force / --reset: wipe the prior demo data, then reseed (an INTENTIONAL reseed in one command).
    # Without it, an already-seeded DB is a clean no-op (exit 0) — never a silent doubling.
    force = any(arg in ("--force", "--reset") for arg in sys.argv[1:])
    try:
        ids = seed(force=force)
    except SeedExistsError as exc:
        print(
            f"DB already seeded — {exc.n_participants} participants present. "
            "Re-run with --force to wipe-and-reseed, or DROP DATABASE first.",
            file=sys.stderr,
        )
        return  # clean skip (exit 0): an accidental re-run is a NO-OP, not an error
    print(json.dumps(ids, indent=2))


if __name__ == "__main__":
    main()
