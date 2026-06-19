"""Gate 7.1 — the isolated shell starts + the Guide's OWN sink works (no real resource touched).

The app imports + a health route responds without opening any connection to the participant
Postgres, Vault, or Redis (it has none). The Guide's message_events table matches the
observability.md schema but is its OWN model (module under guide.*), written to its OWN store.
"""

from __future__ import annotations

from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session

from guide.app import create_app
from guide.observability import (
    GuideMessageEvent,
    create_sink_engine,
    init_sink,
    write_message_event,
)

# The observability.md message_events schema (logical field set) the Guide sink mirrors.
_OBSERVABILITY_FIELDS = {
    "id",
    "conversation_id",
    "request_id",
    "sponsor_id",
    "role_or_guest_scope",
    "surface",
    "route_or_agent",
    "guardrail_decision",
    "retrieved_chunks",
    "llm_provider_model",
    "latency_ms",
    "token_cost_estimate",
    "prompt_tokens",
    "completion_tokens",
    "status",
    "redacted_user_msg",
    "redacted_assistant_msg",
    "ts",
}


def test_health_and_landing_respond() -> None:
    client = TestClient(create_app())
    r = client.get("/healthz")
    assert r.status_code == 200 and r.json()["status"] == "ok"
    # Landing is the static public chat page served BY the Guide (HTML, not JSON).
    r2 = client.get("/")
    assert r2.status_code == 200
    assert "text/html" in r2.headers["content-type"]
    assert "Vigil Guide" in r2.text and "/ask" in r2.text


def test_landing_page_couples_only_to_guide_ask() -> None:
    html = (
        Path(__file__).resolve().parents[2] / "guide" / "static" / "index.html"
    ).read_text(encoding="utf-8")
    # Talks ONLY to the Guide's own same-origin /ask — never the real app's API surface.
    assert "/ask" in html
    for forbidden in (
        "/api/v1",
        ":8000",
        "localhost:8000",
        "/cohort",
        "/participants",
        "/monitoring",
        "/auth",
    ):
        assert forbidden not in html, (
            f"landing page must not reference the real app: {forbidden}"
        )


def test_sink_schema_matches_observability_and_is_guide_owned() -> None:
    cols = set(GuideMessageEvent.__table__.columns.keys())
    assert cols == _OBSERVABILITY_FIELDS, (
        f"schema drift vs observability.md: {cols ^ _OBSERVABILITY_FIELDS}"
    )
    # Own model, not vigil.db.models.
    assert GuideMessageEvent.__module__.startswith("guide."), (
        "the Guide sink model must be defined in guide.*, not imported from vigil.db"
    )


def test_write_to_own_sink_roundtrip() -> None:
    engine = create_sink_engine("sqlite+pysqlite:///:memory:")
    init_sink(engine)
    with Session(engine) as session:
        write_message_event(
            session,
            conversation_id="conv-1",
            request_id="req-1",
            guardrail_decision="blocked",
            status="refused",
            redacted_user_msg="[REDACTED]",
            redacted_assistant_msg="[REDACTED] refusal",
        )
        session.commit()
        rows = session.execute(select(GuideMessageEvent)).scalars().all()
    assert len(rows) == 1
    row = rows[0]
    # Fixed guest semantics: always public_guide surface, always NULL sponsor.
    assert row.surface == "public_guide" and row.sponsor_id is None
    assert row.guardrail_decision == "blocked" and row.status == "refused"
    # No raw text exists — only the redacted fields were stored.
    assert row.redacted_user_msg == "[REDACTED]"
