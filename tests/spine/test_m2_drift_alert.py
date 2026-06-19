"""Gate M2 — drift breach → ALERT the ML/platform engineer.

Step 2 of the human-in-the-loop MLOps loop (M1 detect done; M3 governed promotion next). Proves:

- The PRODUCER alerts ONCE per breach EVENT: a breaching ``compute_drift`` run enqueues exactly one
  ``notify_drift_breach``; a re-run over the SAME ongoing breach does NOT re-enqueue (the
  not-breached → breached EDGE dedupe); a non-breached run enqueues none.
- The SEND path routes the PII-free alert to the model-lifecycle engineer(s) (``platform_admin`` +
  ``mlops_engineer``, Gate DRIFT-EMAIL-FIX), NOT a site coordinator; send-once (the ``notified``
  flag), no double-send on a re-run; a send failure leaves ``notified`` false so Arq retries.
- A constructed-demo breach is labelled a DEMONSTRATION in the alert.
- The recipient address is SEED/ENV-driven (``VIGIL_DEMO_ML_NOTIFY_EMAIL``), never a code constant.

CI-hermetic: the email sender is stubbed (``VIGIL_EMAIL_STUB``); real Postgres via ``migrated_db``.
Cleans ``drift_metric`` + resets the seeded platform_admin address around each test (session-scoped
DB) so a fresh fixture's honest-empty / no-recipient assertions stay valid.
"""

from __future__ import annotations

import asyncio
import uuid
from pathlib import Path

from vigil.services import notification_service

_CHAMPION_MV = "sequence_v1.1:demo"


def _run(coro):  # type: ignore[no-untyped-def]
    """Run an async task on a fresh loop, then LEAVE a usable current loop set.

    ``asyncio.run`` sets the current loop to None on exit, which breaks any later test that uses the
    (deprecated) ``asyncio.get_event_loop()`` pattern in the same process. We restore a fresh loop so
    this test never pollutes the suite's async state.
    """
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()
        asyncio.set_event_loop(asyncio.new_event_loop())


# ---------------------------------------------------------------------------
# stub senders (capturing / failing) — NO network, NO credential
# ---------------------------------------------------------------------------


class _CapturingSender:
    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []

    def send(self, *, to: list[str], subject: str, body: str) -> None:
        self.sent.append({"to": list(to), "subject": subject, "body": body})


class _FailingSender:
    def __init__(self) -> None:
        self.calls = 0

    def send(self, *, to: list[str], subject: str, body: str) -> None:
        self.calls += 1
        from vigil.services.email_sender import EmailSendError

        raise EmailSendError("simulated SMTP failure")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------


def _clear_drift() -> None:
    from sqlalchemy import text

    from vigil.repositories.session import platform_session

    with platform_session() as session:
        session.execute(text("DELETE FROM drift_metric"))


def _band(score: float) -> str:
    return "high" if score > 0.6 else "medium" if score > 0.3 else "low"


def _seed_old_champion_scores(ids: dict[str, str], *, n: int = 40) -> None:
    """Seed n OLD champion scores so the producer has enough data for a reference/current split."""
    from datetime import datetime, timedelta, timezone

    from vigil.db.models import ParticipantScore
    from vigil.repositories.session import sponsor_bootstrap_session

    base = datetime.now(tz=timezone.utc) - timedelta(days=2)
    with sponsor_bootstrap_session(ids["sponsor_a"]) as session:
        for i in range(n):
            score = round((i % 10) / 10 * 0.5, 4)  # spread across [0.0, 0.45]
            session.add(
                ParticipantScore(
                    participant_id=uuid.UUID(ids["participant_a"]),
                    sponsor_id=uuid.UUID(ids["sponsor_a"]),
                    trial_id=uuid.UUID(ids["trial_a"]),
                    site_id=uuid.UUID(ids["site_a"]),
                    risk_score=score,
                    risk_band=_band(score),
                    top_factors=[],
                    reasons=[],
                    model_version=_CHAMPION_MV,
                    model_card_ref="data/models/t2d/model_card.md",
                    synthetic=True,
                    computed_at=base + timedelta(minutes=i),
                )
            )
        session.flush()


def _set_user_notify(user_id: str, email: str | None) -> None:
    from vigil.db.models import User
    from vigil.repositories.session import platform_session

    with platform_session() as session:
        user = session.get(User, uuid.UUID(user_id))
        assert user is not None
        user.notification_email = email
        session.flush()


def _insert_breached_point(
    *, metric: str = "psi", constructed_demo: bool = False, value: float = 0.83
) -> str:
    """Insert ONE breached drift point directly; return its id (the alert anchor)."""
    from vigil.repositories import drift as drift_repo
    from vigil.repositories.session import platform_session

    dist = "champion_risk_score"
    if constructed_demo:
        dist = "champion_risk_score (constructed +0.40 shift demo)"
    with platform_session() as session:
        row = drift_repo.insert_drift_point(
            session,
            regime="t2d",
            model_version=_CHAMPION_MV,
            distribution=dist,
            metric=metric,
            value=value,
            threshold=0.2,
            breached=True,
            reference_n=20,
            current_n=20,
            synthetic=True,
            constructed_demo=constructed_demo,
            note="CONSTRUCTED demonstration" if constructed_demo else "real",
        )
        return str(row.id)


def _point_notified(point_id: str) -> bool:
    from vigil.repositories import drift as drift_repo
    from vigil.repositories.session import platform_session

    with platform_session() as session:
        point = drift_repo.get_drift_point(session, uuid.UUID(point_id))
        assert point is not None
        return bool(point.notified)


# ---------------------------------------------------------------------------
# 1. PRODUCER: a breach enqueues exactly ONE alert; a re-run over the SAME breach does not
# ---------------------------------------------------------------------------


def test_producer_alerts_once_per_breach_event(
    migrated_db: dict[str, str], monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    from vigil.workers.tasks import compute_drift

    ids = migrated_db
    _clear_drift()
    _seed_old_champion_scores(ids)

    calls: list[tuple[str, dict]] = []

    async def _fake_enqueue(function: str, *args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append((function, kwargs))
        return "job-fake"

    monkeypatch.setattr("vigil.workers.settings.enqueue", _fake_enqueue)

    # Run 1: a +0.40 constructed shift breaches → exactly one drift-breach alert enqueued.
    s1 = _run(compute_drift({}, regime="t2d", demo_shift=0.4))
    assert s1["breached"] is True
    assert s1["alert_point_id"], "a fresh breach must carry an alert anchor point"
    drift_alerts = [c for c in calls if c[0] == "notify_drift_breach"]
    assert len(drift_alerts) == 1, "a breach event must enqueue exactly ONE alert"
    assert drift_alerts[0][1]["drift_point_id"] == s1["alert_point_id"]

    # Run 2: still breaching (same ongoing breach) → NO new alert (the edge already fired).
    s2 = _run(compute_drift({}, regime="t2d", demo_shift=0.4))
    assert s2["breached"] is True
    assert s2["alert_point_id"] is None, "an ongoing breach must NOT re-alert"
    assert len([c for c in calls if c[0] == "notify_drift_breach"]) == 1, (
        "a re-run over the same ongoing breach must not enqueue a second alert"
    )

    _clear_drift()


# ---------------------------------------------------------------------------
# 2. PRODUCER: a non-breached run alerts none
# ---------------------------------------------------------------------------


def test_producer_no_alert_when_not_breached(
    migrated_db: dict[str, str], monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """A non-breached run enqueues NO alert. Deterministic: the real champion-score pool is shared
    across the session-scoped DB (other tests add scores), so we pin the producer to a NON-breached
    summary (alert_point_id=None) and assert the enqueue GATE — the exact logic M2 adds — stays shut.
    The breaching path is covered by ``test_producer_alerts_once_per_breach_event`` over real data.
    """
    from vigil.services import drift_service
    from vigil.services.drift_service import DriftRunSummary
    from vigil.workers.tasks import compute_drift

    calls: list[tuple[str, dict]] = []

    async def _fake_enqueue(function: str, *args, **kwargs):  # type: ignore[no-untyped-def]
        calls.append((function, kwargs))
        return "job-fake"

    monkeypatch.setattr("vigil.workers.settings.enqueue", _fake_enqueue)
    monkeypatch.setattr(
        drift_service,
        "run_drift_detection",
        lambda **kw: DriftRunSummary(
            "t2d", _CHAMPION_MV, "computed", 20, 20, 2, False, False, None
        ),
    )

    summary = _run(compute_drift({}, regime="t2d", demo_shift=0.0))
    assert summary["breached"] is False
    assert summary["alert_point_id"] is None
    assert not any(c[0] == "notify_drift_breach" for c in calls), (
        "a non-breached run must enqueue no alert"
    )


# ---------------------------------------------------------------------------
# 3. SEND: once to the PLATFORM recipient, PII-free body, send-once (no double-send)
# ---------------------------------------------------------------------------


def test_send_once_to_platform_engineer_pii_free(
    migrated_db: dict[str, str], monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    ids = migrated_db
    _clear_drift()
    _set_user_notify(ids["platform_admin"], "ml.eng@example.test")
    _set_user_notify(
        ids["mlops_engineer"], None
    )  # isolate the single controlled recipient
    point_id = _insert_breached_point(metric="psi", value=0.83)

    sender = _CapturingSender()
    monkeypatch.setattr(notification_service, "get_email_sender", lambda: sender)

    r1 = notification_service.notify_drift_breach(drift_point_id=point_id)
    assert r1["status"] == "sent" and r1["n_recipients"] == 1
    assert len(sender.sent) == 1
    assert sender.sent[0]["to"] == ["ml.eng@example.test"], (
        "the drift alert must route to the platform/ML engineer only"
    )
    assert _point_notified(point_id) is True

    text = str(sender.sent[0]["subject"]) + "\n" + str(sender.sent[0]["body"])
    # Operational, model-level content carried.
    assert "PSI" in text
    assert "0.8300" in text and "0.2000" in text, "value vs threshold must be carried"
    assert "champion_risk_score" in text
    assert "reference_n=20" in text and "current_n=20" in text
    assert "synthetic=True" in text
    # PII-free by construction: no tenant/participant identifiers exist in drift — assert none leak.
    for leak in (
        ids["participant_a"],
        ids["sponsor_a"],
        ids["trial_a"],
        ids["site_a"],
    ):
        assert leak not in text, "no tenant/participant id may appear in a drift alert"

    # Re-run (a re-fire / retried job) must NOT re-send — the notified guard.
    r2 = notification_service.notify_drift_breach(drift_point_id=point_id)
    assert r2["status"] == "already_notified"
    assert len(sender.sent) == 1, "a re-run must not double-send"

    _set_user_notify(ids["platform_admin"], None)
    _clear_drift()


# ---------------------------------------------------------------------------
# 4. SCOPE: a site coordinator is NEVER a drift recipient (platform-scoped routing)
# ---------------------------------------------------------------------------


def test_recipient_is_platform_scoped_not_coordinator(
    migrated_db: dict[str, str], monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    ids = migrated_db
    _clear_drift()
    _set_user_notify(ids["platform_admin"], "ml.eng@example.test")
    _set_user_notify(
        ids["mlops_engineer"], None
    )  # isolate the single controlled recipient
    _set_user_notify(ids["coordinator_a"], "coord.a.notify@example.test")
    point_id = _insert_breached_point()

    sender = _CapturingSender()
    monkeypatch.setattr(notification_service, "get_email_sender", lambda: sender)

    notification_service.notify_drift_breach(drift_point_id=point_id)
    assert len(sender.sent) == 1
    to = sender.sent[0]["to"]
    assert to == ["ml.eng@example.test"], (
        f"drift routing leaked beyond the platform engineer (a coordinator got it): {to}"
    )

    _set_user_notify(ids["platform_admin"], None)
    _set_user_notify(ids["coordinator_a"], None)
    _clear_drift()


# ---------------------------------------------------------------------------
# 4b. The MLOps engineer (RBAC-OPS) IS a drift recipient (DRIFT-EMAIL-FIX)
# ---------------------------------------------------------------------------


def test_mlops_engineer_is_a_drift_recipient(
    migrated_db: dict[str, str], monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    """The mlops_engineer — the role that OWNS the model lifecycle (drift-run/register/promote) — is
    a drift-alert recipient, by ROLE, even with NO platform_admin address set. This is the gap
    DRIFT-EMAIL-FIX closes (the demo's 'ML engineer' persona is now mlops_engineer)."""
    ids = migrated_db
    _clear_drift()
    _set_user_notify(
        ids["platform_admin"], None
    )  # ONLY the mlops engineer has an address
    _set_user_notify(ids["mlops_engineer"], "mlops.eng@example.test")
    point_id = _insert_breached_point()

    sender = _CapturingSender()
    monkeypatch.setattr(notification_service, "get_email_sender", lambda: sender)

    r = notification_service.notify_drift_breach(drift_point_id=point_id)
    assert r["status"] == "sent" and r["n_recipients"] == 1
    assert sender.sent[0]["to"] == ["mlops.eng@example.test"], (
        "the mlops_engineer must receive the drift alert (model-lifecycle owner)"
    )

    # restore the seeded demo default so later/repeat runs see the fresh-seed state
    _set_user_notify(ids["mlops_engineer"], "mlops@vigil.example")
    _clear_drift()


# ---------------------------------------------------------------------------
# 5. CONSTRUCTED-DEMO breach: the alert is labelled a demonstration
# ---------------------------------------------------------------------------


def test_constructed_demo_breach_alert_is_labelled(
    migrated_db: dict[str, str], monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    ids = migrated_db
    _clear_drift()
    _set_user_notify(ids["platform_admin"], "ml.eng@example.test")
    point_id = _insert_breached_point(metric="ks", constructed_demo=True, value=0.6)

    sender = _CapturingSender()
    monkeypatch.setattr(notification_service, "get_email_sender", lambda: sender)

    notification_service.notify_drift_breach(drift_point_id=point_id)
    assert len(sender.sent) == 1
    text = str(sender.sent[0]["subject"]) + "\n" + str(sender.sent[0]["body"])
    assert "CONSTRUCTED" in text and "demonstration" in text.lower(), (
        "a constructed-demo breach must be labelled a demonstration, not observed drift"
    )
    assert "constructed_demo=True" in text

    _set_user_notify(ids["platform_admin"], None)
    _clear_drift()


# ---------------------------------------------------------------------------
# 6. NO recipient → no send, notified stays false
# ---------------------------------------------------------------------------


def test_no_recipient_no_send(migrated_db: dict[str, str], monkeypatch) -> None:  # type: ignore[no-untyped-def]
    ids = migrated_db
    _clear_drift()
    _set_user_notify(ids["platform_admin"], None)  # no platform engineer address
    _set_user_notify(
        ids["mlops_engineer"], None
    )  # nor the mlops engineer (clear the seeded default)
    point_id = _insert_breached_point()

    sender = _CapturingSender()
    monkeypatch.setattr(notification_service, "get_email_sender", lambda: sender)

    r = notification_service.notify_drift_breach(drift_point_id=point_id)
    assert r["status"] == "no_recipients"
    assert sender.sent == []
    assert _point_notified(point_id) is False, "no recipient → notified stays false"

    _clear_drift()


# ---------------------------------------------------------------------------
# 7. FAILURE handling: a send failure does NOT flip notified (so the Arq job retries)
# ---------------------------------------------------------------------------


def test_send_failure_does_not_flip_notified(
    migrated_db: dict[str, str], monkeypatch
) -> None:  # type: ignore[no-untyped-def]
    from vigil.services.email_sender import EmailSendError
    from vigil.workers.tasks import notify_drift_breach

    ids = migrated_db
    _clear_drift()
    _set_user_notify(ids["platform_admin"], "ml.eng@example.test")
    point_id = _insert_breached_point()

    failing = _FailingSender()
    monkeypatch.setattr(notification_service, "get_email_sender", lambda: failing)

    raised = False
    try:
        _run(notify_drift_breach({"job_try": 1}, drift_point_id=point_id))
    except EmailSendError:
        raised = True
    assert raised, "a send failure must propagate so Arq retries"
    assert failing.calls == 1
    assert _point_notified(point_id) is False, (
        "notified must NOT flip on a failed send (so the retry can deliver)"
    )

    _set_user_notify(ids["platform_admin"], None)
    _clear_drift()


# ---------------------------------------------------------------------------
# 8. The drift recipient address is SEED/ENV-driven, never a committed code constant
# ---------------------------------------------------------------------------


def test_drift_recipient_is_env_seeded_not_constant() -> None:
    """Grep-assert: the platform_admin's notification_email is wired to the env-derived value
    (VIGIL_DEMO_ML_NOTIFY_EMAIL), NOT a hardcoded email literal in application code."""
    src = Path("vigil/seed.py").read_text(encoding="utf-8")
    assert 'os.environ.get("VIGIL_DEMO_ML_NOTIFY_EMAIL")' in src, (
        "the ML-engineer drift address must be sourced from the environment at seed time"
    )
    assert "notification_email=_DEMO_ML_NOTIFY_EMAIL" in src, (
        "the platform_admin's notification_email must be the env-derived seed value, not a constant"
    )
