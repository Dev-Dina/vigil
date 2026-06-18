"""Gate 9.6 — the PII-free serious-risk email notifier (SMTP send on a crossing).

Stubbed sender, NO real email. Proves: send-once (the ``notified`` flag; no re-send / no
double-send on a re-run), a PII-FREE body (structural — none of the participant id / coded_ref /
risk score / factor appears), SCOPE-BOUND recipients re-proven AT THE SEND PATH (site-2 / a
different sponsor are NEVER recipients for a site-1 crossing), failure handling (a send failure
does NOT flip ``notified`` so the Arq job can retry), and CI-hermetic (stub sender, no credential).

Real Postgres via migrated_db.
"""

from __future__ import annotations

import asyncio
import uuid

from vigil.services import notification_service

_CHAMPION_MV = "sequence_v1.1:demo"
_CARD = "data/models/t2d/model_card.md"


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


def _make_trial_site_participant(
    sponsor_id: uuid.UUID, *, coded_ref: str = "PT-NOTIFY"
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    from vigil.db.models import Participant, Site, Trial
    from vigil.repositories.session import sponsor_bootstrap_session

    with sponsor_bootstrap_session(str(sponsor_id)) as session:
        t = Trial(sponsor_id=sponsor_id, name="notify-trial")
        session.add(t)
        session.flush()
        s = Site(sponsor_id=sponsor_id, trial_id=t.id, name="notify-site")
        session.add(s)
        session.flush()
        p = Participant(
            sponsor_id=sponsor_id,
            trial_id=t.id,
            site_id=s.id,
            coded_ref=coded_ref,
            status="active",
        )
        session.add(p)
        session.flush()
        return t.id, s.id, p.id


def _make_site(sponsor_id: uuid.UUID, trial_id: uuid.UUID, name: str) -> uuid.UUID:
    from vigil.db.models import Site
    from vigil.repositories.session import sponsor_bootstrap_session

    with sponsor_bootstrap_session(str(sponsor_id)) as session:
        s = Site(sponsor_id=sponsor_id, trial_id=trial_id, name=name)
        session.add(s)
        session.flush()
        return s.id


def _make_crossing(
    sponsor_id: uuid.UUID,
    trial_id: uuid.UUID,
    site_id: uuid.UUID,
    pid: uuid.UUID,
    *,
    risk_score: float = 0.85,
) -> uuid.UUID:
    """Create a real RiskCrossing (via a champion score row) and return its id."""
    from vigil.repositories import scoring as scoring_repo
    from vigil.repositories.session import sponsor_bootstrap_session

    with sponsor_bootstrap_session(str(sponsor_id)) as session:
        score_row = scoring_repo.append_score(
            session,
            participant_id=pid,
            sponsor_id=sponsor_id,
            trial_id=trial_id,
            site_id=site_id,
            risk_score=risk_score,
            risk_band="high",
            top_factors=["consecutive_missed"],
            reasons=[{"feature": "consecutive_missed", "contribution": 0.2}],
            model_version=_CHAMPION_MV,
            model_card_ref=_CARD,
            synthetic=True,
        )
        crossing = scoring_repo.record_crossing(
            session, score_row=score_row, prior_band="medium"
        )
        assert crossing is not None
        return crossing.id


def _make_coordinator(
    *,
    email: str,
    sponsor_id: uuid.UUID,
    trial_id: uuid.UUID,
    site_id: uuid.UUID,
    notif: str,
) -> uuid.UUID:
    from vigil.core.security import hash_password
    from vigil.db.models import User
    from vigil.domain import Role
    from vigil.repositories.session import platform_session

    with platform_session() as session:
        u = User(
            email=email,
            password_hash=hash_password("x"),
            role=str(Role.COORDINATOR),
            home_sponsor_id=sponsor_id,
            home_trial_id=trial_id,
            home_site_id=site_id,
            notification_email=notif,
        )
        session.add(u)
        session.flush()
        return u.id


def _notified(sponsor_id: uuid.UUID, crossing_id: uuid.UUID) -> bool:
    from vigil.repositories import scoring as scoring_repo
    from vigil.repositories.session import sponsor_bootstrap_session

    with sponsor_bootstrap_session(str(sponsor_id)) as session:
        c = scoring_repo.get_crossing(session, crossing_id)
        assert c is not None
        return bool(c.notified)


# ---------------------------------------------------------------------------
# 1. send-once: invokes the sender once + flips notified; a re-run does NOT re-send
# ---------------------------------------------------------------------------


def test_send_once_and_no_resend(migrated_db: dict[str, str], monkeypatch) -> None:  # type: ignore[no-untyped-def]
    ids = migrated_db
    sponsor_a = uuid.UUID(ids["sponsor_a"])
    trial, site, pid = _make_trial_site_participant(sponsor_a)
    crossing_id = _make_crossing(sponsor_a, trial, site, pid)
    _make_coordinator(
        email="notify.s1@vigil.example",
        sponsor_id=sponsor_a,
        trial_id=trial,
        site_id=site,
        notif="s1@example.test",
    )

    sender = _CapturingSender()
    monkeypatch.setattr(notification_service, "get_email_sender", lambda: sender)

    r1 = notification_service.notify_crossing(
        crossing_id=str(crossing_id), sponsor_id=ids["sponsor_a"]
    )
    assert r1["status"] == "sent" and r1["n_recipients"] == 1
    assert len(sender.sent) == 1, "the sender must be invoked exactly once"
    assert _notified(sponsor_a, crossing_id) is True, "notified must flip true on a send"

    # Re-run (a re-fire / retried job) must NOT re-send — the notified guard.
    r2 = notification_service.notify_crossing(
        crossing_id=str(crossing_id), sponsor_id=ids["sponsor_a"]
    )
    assert r2["status"] == "already_notified"
    assert len(sender.sent) == 1, "a re-run must not double-send"


# ---------------------------------------------------------------------------
# 2. no recipient → no send, no crash, notified stays false
# ---------------------------------------------------------------------------


def test_no_recipient_no_send(migrated_db: dict[str, str], monkeypatch) -> None:  # type: ignore[no-untyped-def]
    ids = migrated_db
    sponsor_a = uuid.UUID(ids["sponsor_a"])
    trial, site, pid = _make_trial_site_participant(sponsor_a, coded_ref="PT-NOREC")
    crossing_id = _make_crossing(sponsor_a, trial, site, pid)
    # No coordinator with a notification_email exists for this fresh site.

    sender = _CapturingSender()
    monkeypatch.setattr(notification_service, "get_email_sender", lambda: sender)

    r = notification_service.notify_crossing(
        crossing_id=str(crossing_id), sponsor_id=ids["sponsor_a"]
    )
    assert r["status"] == "no_recipients"
    assert sender.sent == [], "no send when there is no in-scope recipient"
    assert _notified(sponsor_a, crossing_id) is False, "no recipient → notified stays false"


# ---------------------------------------------------------------------------
# 3. PII-FREE body (structural): no participant id / coded_ref / risk score / factor
# ---------------------------------------------------------------------------


def test_body_is_pii_free(migrated_db: dict[str, str], monkeypatch) -> None:  # type: ignore[no-untyped-def]
    ids = migrated_db
    sponsor_a = uuid.UUID(ids["sponsor_a"])
    coded_ref = "PT-PIITEST-9999"
    trial, site, pid = _make_trial_site_participant(sponsor_a, coded_ref=coded_ref)
    crossing_id = _make_crossing(sponsor_a, trial, site, pid, risk_score=0.91)
    _make_coordinator(
        email="notify.pii@vigil.example",
        sponsor_id=sponsor_a,
        trial_id=trial,
        site_id=site,
        notif="pii@example.test",
    )

    sender = _CapturingSender()
    monkeypatch.setattr(notification_service, "get_email_sender", lambda: sender)
    notification_service.notify_crossing(
        crossing_id=str(crossing_id), sponsor_id=ids["sponsor_a"]
    )
    assert len(sender.sent) == 1
    text = str(sender.sent[0]["subject"]) + "\n" + str(sender.sent[0]["body"])

    # STRUCTURAL: the actual identifiers / numbers must be ABSENT (not a regex afterthought).
    assert str(pid) not in text, "participant id leaked into the email body"
    assert coded_ref not in text, "coded_ref leaked into the email body"
    assert "0.91" not in text, "risk score leaked into the email body"
    assert "consecutive_missed" not in text, "a risk factor leaked into the email body"

    # POSITIVE: the doorbell content + deep link + synthetic label (the crossing is synthetic).
    assert "serious-risk threshold" in text
    assert "/at-risk" in text, "the deep link to the at-risk surface must be present"
    assert "synthetic-data demonstration" in text, "synthetic signal must be labelled"


# ---------------------------------------------------------------------------
# 4. SCOPE-BOUND at the SEND path: only the in-scope site's coordinator is a recipient
# ---------------------------------------------------------------------------


def test_send_is_scope_bound(migrated_db: dict[str, str], monkeypatch) -> None:  # type: ignore[no-untyped-def]
    ids = migrated_db
    sponsor_a = uuid.UUID(ids["sponsor_a"])
    sponsor_b = uuid.UUID(ids["sponsor_b"])
    trial, site1, pid = _make_trial_site_participant(sponsor_a)
    crossing_id = _make_crossing(sponsor_a, trial, site1, pid)
    site2 = _make_site(sponsor_a, trial, "notify-site-2")

    _make_coordinator(
        email="send.site1@vigil.example",
        sponsor_id=sponsor_a,
        trial_id=trial,
        site_id=site1,
        notif="send.site1@example.test",
    )
    _make_coordinator(
        email="send.site2@vigil.example",
        sponsor_id=sponsor_a,
        trial_id=trial,
        site_id=site2,
        notif="send.site2@example.test",
    )
    tb, sb, _pb = _make_trial_site_participant(sponsor_b, coded_ref="PT-B")
    _make_coordinator(
        email="send.sponsorb@vigil.example",
        sponsor_id=sponsor_b,
        trial_id=tb,
        site_id=sb,
        notif="send.sponsorb@example.test",
    )

    sender = _CapturingSender()
    monkeypatch.setattr(notification_service, "get_email_sender", lambda: sender)
    notification_service.notify_crossing(
        crossing_id=str(crossing_id), sponsor_id=ids["sponsor_a"]
    )

    assert len(sender.sent) == 1
    to = sender.sent[0]["to"]
    assert to == ["send.site1@example.test"], (
        f"SACRED scope-bound send leaked beyond the in-scope site: {to}"
    )


# ---------------------------------------------------------------------------
# 5. failure handling: a send failure does NOT flip notified (so the job retries)
# ---------------------------------------------------------------------------


def test_send_failure_does_not_flip_notified(migrated_db: dict[str, str], monkeypatch) -> None:  # type: ignore[no-untyped-def]
    from vigil.services.email_sender import EmailSendError
    from vigil.workers.tasks import send_crossing_notification

    ids = migrated_db
    sponsor_a = uuid.UUID(ids["sponsor_a"])
    trial, site, pid = _make_trial_site_participant(sponsor_a, coded_ref="PT-FAIL")
    crossing_id = _make_crossing(sponsor_a, trial, site, pid)
    _make_coordinator(
        email="notify.fail@vigil.example",
        sponsor_id=sponsor_a,
        trial_id=trial,
        site_id=site,
        notif="fail@example.test",
    )

    failing = _FailingSender()
    monkeypatch.setattr(notification_service, "get_email_sender", lambda: failing)

    # The Arq job re-raises so the framework retries (bounded by WorkerSettings.max_tries).
    raised = False
    try:
        asyncio.get_event_loop().run_until_complete(
            send_crossing_notification(
                {"job_try": 1}, crossing_id=str(crossing_id), sponsor_id=ids["sponsor_a"]
            )
        )
    except EmailSendError:
        raised = True
    assert raised, "a send failure must propagate so Arq retries"
    assert failing.calls == 1
    assert _notified(sponsor_a, crossing_id) is False, (
        "notified must NOT flip on a failed send (so the retry can deliver)"
    )


# ---------------------------------------------------------------------------
# 6. CI-hermetic: the default stub sender sends nothing real and needs no credential
# ---------------------------------------------------------------------------


def test_hermetic_stub_sender_default() -> None:
    """With VIGIL_EMAIL_STUB (conftest default true), the factory returns the StubEmailSender —
    no smtplib, no Vault App Password read."""
    from vigil.services.email_sender import StubEmailSender, get_email_sender

    sender = get_email_sender()
    assert isinstance(sender, StubEmailSender)
    # The stub records intent and makes no network call / reads no credential.
    sender.send(to=["x@example.test"], subject="s", body="b")
    assert sender.sent and sender.sent[0]["to"] == ["x@example.test"]
