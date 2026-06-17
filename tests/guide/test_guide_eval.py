"""Gate 7.4 — the Guide RAG eval set as a release gate (specs/rag.md § Guide set).

Scored hermetically (offline embedder + stub LLM). Grounded cases must answer with an approved-doc
citation (the right source where pinned); unanswerable / low-relevance / out-of-scope cases must
REFUSE — the public-faithfulness contract. Registered in scripts/check_specs.py (mirroring the
5.7 local-assistant eval-set enforcement) so the Guide can't ship without it.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from guide.embeddings import get_embedder
from guide.llm import get_guide_llm_client
from guide.rag import answer_question

_EVAL = Path(__file__).resolve().parents[2] / "tests" / "eval" / "guide_eval.json"


def _cases() -> list[dict]:
    data = json.loads(_EVAL.read_text(encoding="utf-8"))
    assert data["version"] >= 1 and data["surface"] == "public_guide"
    return data["cases"]


@pytest.mark.parametrize("case", _cases(), ids=lambda c: c["id"])
def test_guide_eval_case(case, guide_index) -> None:  # type: ignore[no-untyped-def]
    ans = answer_question(
        case["prompt"],
        index=guide_index,
        embedder=get_embedder(),
        llm=get_guide_llm_client(),
    )
    if case["expect"] == "grounded":
        assert ans.status == "ok", f"{case['id']} should be grounded, got refusal"
        assert ans.citations, f"{case['id']} grounded answer must cite approved docs"
        src = case.get("expect_source_contains")
        if src:
            assert any(src in c["source_id"] for c in ans.citations), (
                f"{case['id']} expected a citation containing {src!r}; got {ans.citations}"
            )
    else:  # refused
        assert ans.status == "refused", f"{case['id']} should refuse, got an answer"
        assert not ans.citations
