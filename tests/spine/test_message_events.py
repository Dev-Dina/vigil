"""Gate 5.1 — message_events table + redacted-write path (specs/observability.md).

message_events is CROSS-TENANT BY ROLE (domain.md, like audit_log): a sponsor session sees only
its own rows; platform/auditor see all; null-sponsor (Guide-class) rows are platform-visible ONLY.
This is the audit_log-pattern leakage test applied to message_events, plus the write-path round
trip and the MessageEventOut (api.md) shape alignment.

Real Postgres via migrated_db (RLS is a DB feature).
"""

from __future__ import annotations

import uuid

from sqlalchemy import select

from vigil.db.models import MessageEvent
from vigil.repositories import observability as obs_repo
from vigil.repositories.session import platform_session, sponsor_bootstrap_session


def _all_ids(session) -> set[str]:  # type: ignore[no-untyped-def]
    return {str(r.id) for r in session.execute(select(MessageEvent)).scalars()}


# ---------------------------------------------------------------------------
# 1. Cross-tenant-by-role visibility (the sacred surface)
# ---------------------------------------------------------------------------


def test_message_events_cross_tenant_by_role(migrated_db: dict[str, str]) -> None:
    ids = migrated_db
    sponsor_a = uuid.UUID(ids["sponsor_a"])

    # Sponsor-A turn (written under A's RLS session).
    with sponsor_bootstrap_session(ids["sponsor_a"]) as session:
        row_a = obs_repo.write_message_event(
            session,
            conversation_id=uuid.uuid4(),
            request_id="req-a",
            sponsor_id=sponsor_a,
            role_or_guest_scope="coordinator",
            surface="local_assistant",
            guardrail_decision="allowed",
            status="ok",
            redacted_user_msg="[REDACTED] why is PT flagged",
            redacted_assistant_msg="[REDACTED] grounded answer",
        )
        id_a = str(row_a.id)

    # Null-sponsor (Guide-class) turn — WITH CHECK passes only under is_platform.
    with platform_session() as session:
        row_guide = obs_repo.write_message_event(
            session,
            conversation_id=uuid.uuid4(),
            request_id="req-guide",
            sponsor_id=None,
            role_or_guest_scope="guest",
            surface="public_guide",
            guardrail_decision="blocked",
            status="refused",
            redacted_user_msg="[REDACTED] jailbreak attempt",
        )
        id_guide = str(row_guide.id)

    # Sponsor B cannot see A's row, nor the null-sponsor Guide row.
    with sponsor_bootstrap_session(ids["sponsor_b"]) as session:
        visible_b = _all_ids(session)
    assert id_a not in visible_b, (
        "Sponsor B saw Sponsor A's message_event — cross-tenant leak"
    )
    assert id_guide not in visible_b, "Sponsor B saw a null-sponsor Guide row — leak"

    # Sponsor A sees its own row, but NOT the null-sponsor Guide row.
    with sponsor_bootstrap_session(ids["sponsor_a"]) as session:
        visible_a = _all_ids(session)
    assert id_a in visible_a, "Sponsor A could not see its own message_event"
    assert id_guide not in visible_a, (
        "Sponsor A saw a null-sponsor Guide row — null-sponsor must be platform-only"
    )

    # Platform/auditor session sees both (cross-tenant by role).
    with platform_session() as session:
        visible_p = _all_ids(session)
    assert {id_a, id_guide} <= visible_p, "platform session must see all message_events"


# ---------------------------------------------------------------------------
# 2. Write path round-trip + schema fields (observability.md)
# ---------------------------------------------------------------------------


def test_message_event_write_roundtrip_schema(migrated_db: dict[str, str]) -> None:
    ids = migrated_db
    sponsor_a = uuid.UUID(ids["sponsor_a"])
    conv = uuid.uuid4()

    with sponsor_bootstrap_session(ids["sponsor_a"]) as session:
        written = obs_repo.write_message_event(
            session,
            conversation_id=conv,
            request_id="req-roundtrip",
            sponsor_id=sponsor_a,
            role_or_guest_scope="study_manager",
            surface="local_assistant",
            route_or_agent="retention_agent",
            guardrail_decision="allowed",
            retrieved_chunks=[
                {
                    "source_type": "structured",
                    "source_id": "participant:risk",
                    "locator": "v1",
                }
            ],
            llm_provider_model="openrouter/free-model",
            latency_ms=1234,
            token_cost_estimate=0.000125,
            status="ok",
            redacted_user_msg="[REDACTED] msg",
            redacted_assistant_msg="[REDACTED] resp",
        )
        wid = str(written.id)

    with sponsor_bootstrap_session(ids["sponsor_a"]) as session:
        row = session.get(MessageEvent, uuid.UUID(wid))
        assert row is not None, "written message_event not readable back in scope"
        # Every observability.md column present + round-tripped.
        assert str(row.conversation_id) == str(conv)
        assert row.request_id == "req-roundtrip"
        assert str(row.sponsor_id) == ids["sponsor_a"]
        assert row.role_or_guest_scope == "study_manager"
        assert row.surface == "local_assistant"
        assert row.route_or_agent == "retention_agent"
        assert row.guardrail_decision == "allowed"
        assert row.retrieved_chunks[0]["source_type"] == "structured"
        assert row.llm_provider_model == "openrouter/free-model"
        assert row.latency_ms == 1234
        assert float(row.token_cost_estimate) == 0.000125
        assert row.status == "ok"
        assert row.redacted_user_msg == "[REDACTED] msg"
        assert row.redacted_assistant_msg == "[REDACTED] resp"
        assert row.ts is not None


# ---------------------------------------------------------------------------
# 3. Shape alignment with MessageEventOut (api.md) — read shape is Phase 6; confirm no drift
# ---------------------------------------------------------------------------


def test_message_event_matches_api_read_shape(migrated_db: dict[str, str]) -> None:
    # MessageEventOut (specs/api.md § monitoring, already redacted) fields. The endpoint is
    # Phase 6; here we only confirm the ORM exposes every field it needs (no drift).
    required = {
        "conversation_id",
        "request_id",
        "role_or_guest_scope",
        "guardrail_decision",
        "llm_provider_model",
        "latency_ms",
        "status",
        "ts",
    }
    missing = {f for f in required if not hasattr(MessageEvent, f)}
    assert not missing, f"MessageEvent ORM missing MessageEventOut fields: {missing}"

    # And there is NO raw-content attribute — only redacted columns persist text.
    assert not hasattr(MessageEvent, "user_msg")
    assert not hasattr(MessageEvent, "assistant_msg")
    assert hasattr(MessageEvent, "redacted_user_msg")
    assert hasattr(MessageEvent, "redacted_assistant_msg")
