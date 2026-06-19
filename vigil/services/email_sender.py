"""Email sender for the Phase-9.6 serious-risk doorbell notification.

A NEW outbound egress (SMTP) on the app side, allow-listed deny-by-default alongside the
LLM/Langfuse hosts (specs/isolation.md § Phase 9, specs/infra.md § notification egress). Sent
from an Arq job, NEVER inline in the scoring path.

Stub-first, mirroring the Langfuse tracer (``vigil.agents.tracing``):
- **CI-HERMETIC.** ``get_email_sender`` returns the :class:`StubEmailSender` whenever
  ``email_stub`` (default TRUE) — selected BEFORE any credential read, so CI/tests send NO real
  email and need NO Vault App Password. The live send is an explicit opt-in
  (``VIGIL_EMAIL_STUB=false`` + the Gmail App Password in Vault) and a run-once manual check,
  never a CI gate.
- **PII-FREE BODY (the guarantee).** :func:`build_notification_body` is built ONLY from the deep
  link + the synthetic flag — it never receives a participant id, coded ref, risk score, or any
  factor, so the body is PII-free by construction (specs/isolation.md § Email body).
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from vigil.core.config import get_settings
from vigil.core.logging import get_logger

log = get_logger("vigil.services.email_sender")


class EmailSendError(RuntimeError):
    """A real SMTP send failed. Propagated so the Arq job retries (notified is NOT flipped)."""


def build_notification_body(*, deep_link: str, synthetic: bool) -> tuple[str, str]:
    """The PII-FREE doorbell (subject, body) — fixed text + deep link + synthetic label only.

    Carries NO participant identity, NO coded id, NO clinical detail, NO risk score, NO factors
    (specs/isolation.md § Email body). The deep link points at the authenticated, scope-bound
    at-risk surface — the data lives behind auth and is reached only by logging in.
    """
    subject = "Vigil — a participant has crossed the serious-risk threshold"
    body = (
        "A participant at your site has crossed the serious-risk threshold — "
        "log in to review:\n"
        f"{deep_link}\n"
    )
    if synthetic:
        body += (
            "\nThis is a synthetic-data demonstration — the signal is generated to "
            "demonstrate the method, not a clinical finding.\n"
        )
    return subject, body


def build_drift_alert_body(
    *,
    regime: str,
    model_version: str,
    metric: str,
    value: float,
    threshold: float,
    distribution: str,
    reference_n: int,
    current_n: int,
    synthetic: bool,
    constructed_demo: bool,
    deep_link: str,
) -> tuple[str, str]:
    """The PII-FREE drift-breach ALERT (subject, body) for the ML/platform engineer (Gate M2).

    Drift is a MODEL-level statistic: there is NO participant, coded id, or risk row in it, so the
    body is PII-free by construction — it carries ONLY model-level scalars (which metric breached,
    value vs threshold, the distribution, the window sizes) + provenance + a deep link to the
    platform drift surface. A ``constructed_demo`` breach is labelled a DEMONSTRATION (consistent
    with the M1 provenance) so a demo alert is never mistaken for observed production drift.
    """
    kind = "CONSTRUCTED demo" if constructed_demo else "breach"
    subject = (
        f"Vigil — {metric.upper()} drift {kind} on champion {model_version} ({regime})"
    )
    body = (
        f"A {metric.upper()} drift {('DEMONSTRATION' if constructed_demo else 'breach')} "
        f"was detected on the champion model {model_version} (regime {regime}).\n\n"
        f"  metric:       {metric.upper()}\n"
        f"  value:        {value:.4f}\n"
        f"  threshold:    {threshold:.4f}\n"
        f"  distribution: {distribution}\n"
        f"  window:       reference_n={reference_n}, current_n={current_n}\n"
        f"  provenance:   synthetic={synthetic}, constructed_demo={constructed_demo}\n\n"
        f"Review the drift surface (platform/auditor only — log in):\n{deep_link}\n"
    )
    if constructed_demo:
        body += (
            "\nThis is a CONSTRUCTED demonstration (the current window is a deliberately-shifted "
            "copy of the reference) — it demonstrates the detector firing, NOT observed production "
            "drift.\n"
        )
    elif synthetic:
        body += (
            "\nThis drift was computed over the SYNTHETIC cohort — a method demonstration, not a "
            "clinical signal.\n"
        )
    return subject, body


@runtime_checkable
class EmailSender(Protocol):
    """The only contract the notifier depends on for sending mail."""

    def send(self, *, to: list[str], subject: str, body: str) -> None: ...


class StubEmailSender:
    """Hermetic sender — records the intent, makes NO network call, reads NO credential.

    Used in CI/tests (``email_stub``). Tests inject/patch this to assert what WOULD have been sent.
    """

    def __init__(self) -> None:
        self.sent: list[dict[str, object]] = []

    def send(self, *, to: list[str], subject: str, body: str) -> None:
        self.sent.append({"to": list(to), "subject": subject, "body": body})
        log.info(
            "email.stub_send",
            extra={"extra": {"n_recipients": len(to), "subject": subject}},
        )


class SmtpEmailSender:
    """Real Gmail SMTP sender (stdlib ``smtplib`` + STARTTLS). Built only when email_stub=False.

    Reads the App Password from Vault (``vigil/notifications/email_password``); the host/port/From
    are non-secret config. A failure raises :class:`EmailSendError` so the Arq job retries
    (bounded by the worker ``max_tries``); the caller does NOT flip ``notified`` on failure.
    """

    def __init__(
        self, *, host: str, port: int, from_address: str, password: str
    ) -> None:
        self._host = host
        self._port = port
        self._from = from_address
        self._password = password

    def send(self, *, to: list[str], subject: str, body: str) -> None:
        import smtplib
        from email.message import EmailMessage

        msg = EmailMessage()
        msg["From"] = self._from
        msg["To"] = ", ".join(to)
        msg["Subject"] = subject
        msg.set_content(body)
        try:
            with smtplib.SMTP(self._host, self._port, timeout=30) as smtp:
                smtp.starttls()
                smtp.login(self._from, self._password)
                smtp.send_message(msg)
        except Exception as exc:  # noqa: BLE001 - normalize to a retryable send error
            raise EmailSendError(f"SMTP send failed: {exc}") from exc
        log.info("email.smtp_send", extra={"extra": {"n_recipients": len(to)}})


def get_email_sender() -> EmailSender:
    """Return the active sender: :class:`StubEmailSender` unless ``email_stub`` is False.

    The stub branch is checked FIRST — no credential is read and ``smtplib`` is never used, so
    CI/tests send zero real email and need no Vault App Password (mirrors the Langfuse tracer).
    """
    settings = get_settings()
    if settings.email_stub:
        return StubEmailSender()
    if not settings.notify_from_address:
        raise EmailSendError(
            "notify_from_address must be set for a live send (VIGIL_NOTIFY_FROM_ADDRESS)"
        )
    return SmtpEmailSender(
        host=settings.smtp_host,
        port=settings.smtp_port,
        from_address=settings.notify_from_address,
        password=settings.notify_email_password,
    )
