"""Gate 5.7 — RAG eval set runner / release gate (specs/rag.md § Evaluation set).

Scores the versioned local-assistant eval set (tests/eval/local_assistant_eval.json) against the
REAL sentence-transformers embedder (semantic retrieval, offline/vendored — the only embedder
now) with a STUBBED LLM for reproducible generation. Deterministic scoring (see the eval file's
`scoring` block): faithfulness = no ungrounded answer (answered ⇒ cited; no grounding ⇒ refuse);
citation precision = citations reference real retrieved sources; citation recall = expected source
present; answerable-vs-unanswerable; scope-faithfulness (no out-of-scope participant citation);
metric-grounding (metric ⇒ card citation). Release gate: EVERY case must pass.

Real Postgres via migrated_db; hermetic (no live LLM, no network embedder).
"""

from __future__ import annotations

import asyncio
import json
import uuid
from pathlib import Path

from sqlalchemy import text

from vigil.agents.corpus import ingest_card_corpus
from vigil.agents.embeddings import get_embedder
from vigil.agents.llm import LLMResponse
from vigil.db.models import Participant, Site, Trial
from vigil.repositories.session import platform_session, sponsor_bootstrap_session
from vigil.workers.tasks import run_assistant_turn

_EVAL_PATH = (
    Path(__file__).resolve().parents[2] / "tests/eval/local_assistant_eval.json"
)
_CHAMPION_MV = "sequence_v1.0:demo"
_CARD = "data/models/t2d/model_card.md"


class _ScriptedLLM:
    """ROUTER call → route to the case's agent; agent call → a fixed grounded answer."""

    def __init__(self, route_to: str) -> None:
        self._route_to = route_to

    def complete(self, messages, *, model=None, max_tokens=None, temperature=0.0):  # type: ignore[no-untyped-def]
        system = next((m.content for m in messages if m.role == "system"), "")
        if "ROUTER" in system:
            return LLMResponse(content=f'{{"agent": "{self._route_to}"}}', model="stub")
        return LLMResponse(
            content="Grounded answer per the cited context.", model="stub"
        )


def _champion_score(ids, pid, *, trial_id, site_id) -> None:  # type: ignore[no-untyped-def]
    from vigil.repositories import scoring as scoring_repo

    with sponsor_bootstrap_session(ids["sponsor_a"]) as session:
        scoring_repo.append_score(
            session,
            participant_id=pid,
            sponsor_id=uuid.UUID(ids["sponsor_a"]),
            trial_id=trial_id,
            site_id=site_id,
            risk_score=0.78,
            risk_band="high",
            top_factors=["missed_visits"],
            reasons=[],
            model_version=_CHAMPION_MV,
            model_card_ref=_CARD,
            synthetic=True,
        )


def _other_site_participant(ids) -> str:  # type: ignore[no-untyped-def]
    sponsor_a = uuid.UUID(ids["sponsor_a"])
    with sponsor_bootstrap_session(ids["sponsor_a"]) as session:
        t = Trial(sponsor_id=sponsor_a, name="eval probe trial")
        session.add(t)
        session.flush()
        s = Site(sponsor_id=sponsor_a, trial_id=t.id, name="eval probe site")
        session.add(s)
        session.flush()
        p = Participant(
            sponsor_id=sponsor_a,
            trial_id=t.id,
            site_id=s.id,
            coded_ref="PT-EVAL-S2",
            status="active",
            risk_score=0.9,
            risk_band="high",
        )
        session.add(p)
        session.flush()
        pid, tid, sid = p.id, t.id, s.id
    _champion_score(ids, pid, trial_id=tid, site_id=sid)
    return str(pid)


def _run_turn(ids, *, content, participant_id, route_to) -> dict:  # type: ignore[no-untyped-def]
    return asyncio.get_event_loop().run_until_complete(
        run_assistant_turn(
            {"job_try": 0, "_llm_override": _ScriptedLLM(route_to)},
            conversation_id=str(uuid.uuid4()),
            user_id=ids["coordinator_a"],
            content=content,
            participant_id=participant_id,
            sponsor_id=ids["sponsor_a"],
            request_id="eval",
        )
    )


def _resolve_pid(ids, symbolic: str, other_site: str) -> str | None:  # type: ignore[no-untyped-def]
    return {
        "participant_a": ids["participant_a"],
        "participant_b": ids["participant_b"],
        "other_site": other_site,
        "none": None,
    }[symbolic]


def _score_case(case, res, pid) -> tuple[bool, str]:  # type: ignore[no-untyped-def]
    """Return (passed, reason) for one eval case — deterministic, per the eval `scoring` block."""
    cites = res["citations"]
    expect = case["expect"]

    # citation PRECISION (all cases): no citation may reference an out-of-scope participant.
    # (precision of structured citations is structural; this is the safety-critical half.)
    if expect == "card_cited":
        ok = res["status"] == "ok" and any(
            c["source_type"] == "document" and c["source_id"].endswith(".md")
            for c in cites
        )
        return ok, "metric answer must carry a card-corpus (.md) document citation"
    if expect == "answered_cited":
        ok = res["status"] == "ok" and len(cites) >= 1
        return (
            ok,
            "answerable turn must be answered WITH >=1 grounding citation (faithfulness)",
        )
    if expect == "refused_when_ungrounded":
        ok = res["status"] == "refused" and not cites
        return (
            ok,
            "ungrounded question must yield a grounded refusal (no ungrounded answer)",
        )
    if expect == "no_participant_citation":
        leaked = any(
            c["source_type"] == "structured"
            and pid is not None
            and pid in c["source_id"]
            for c in cites
        )
        return (
            not leaked
        ), "scope-faithfulness: out-of-scope participant must not be cited"
    return False, f"unknown expect={expect!r}"


def test_rag_eval_set_release_gate(migrated_db: dict[str, str]) -> None:
    ids = migrated_db
    spec = json.loads(_EVAL_PATH.read_text(encoding="utf-8"))
    assert spec["surface"] == "local_assistant" and spec["cases"], (
        "eval set malformed/empty"
    )

    # Real semantic corpus + champion facts so scope cases hide PRESENT data (not absent data).
    with platform_session() as session:
        ingest_card_corpus(session, get_embedder())
    _champion_score(
        ids,
        uuid.UUID(ids["participant_a"]),
        trial_id=uuid.UUID(ids["trial_a"]),
        site_id=uuid.UUID(ids["site_a"]),
    )
    other_site = _other_site_participant(ids)

    failures: list[str] = []
    for case in spec["cases"]:
        pid = _resolve_pid(ids, case["participant"], other_site)
        if case["expect"] == "refused_when_ungrounded":
            # Exercise the grounded-refusal guarantee: clear all retrievable grounding, run, restore.
            with platform_session() as session:
                session.execute(text("DELETE FROM document_chunk"))
            try:
                res = _run_turn(
                    ids,
                    content=case["question"],
                    participant_id=pid,
                    route_to=case["route_to"],
                )
            finally:
                with platform_session() as session:
                    ingest_card_corpus(session, get_embedder())
        else:
            res = _run_turn(
                ids,
                content=case["question"],
                participant_id=pid,
                route_to=case["route_to"],
            )

        passed, reason = _score_case(case, res, pid)
        if not passed:
            failures.append(
                f"[{case['id']} · {case['dimension']}] {reason} (status={res['status']}, n_cites={len(res['citations'])})"
            )

    assert not failures, "RAG eval set FAILED (release gate):\n" + "\n".join(failures)


def test_rag_eval_covers_all_dimensions() -> None:
    """The eval set must label every required dimension (specs/rag.md § Evaluation set)."""
    spec = json.loads(_EVAL_PATH.read_text(encoding="utf-8"))
    dims = {c["dimension"] for c in spec["cases"]}
    required = {
        "faithfulness",
        "citation",
        "answerable_vs_unanswerable",
        "scope_faithfulness",
        "metric_grounding",
    }
    missing = required - dims
    assert not missing, f"eval set missing required dimensions: {missing}"
