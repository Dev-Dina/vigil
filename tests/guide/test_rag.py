"""Gate 7.2 — Guide RAG: grounded/cited answer + SOUND refusal (hermetic; stub LLM, offline embed).

Proves the Guide's ONE capability over its OWN file-backed index of the approved docs:
- on-topic → a CITED answer from the approved docs;
- low-relevance (off-topic/tangential) → REFUSE via the relevance threshold (the public-
  faithfulness check — not only zero retrieval);
- zero retrieval → refuse;
- a refusal triggers NO open generation (the LLM is never called).

Threshold tuned from measured similarities: on-topic project questions score ~0.52–0.54, off-topic
≤0.15, a clinical ask ~0.27 — all on opposite sides of the 0.30 threshold.
"""

from __future__ import annotations

from guide.embeddings import GuideEmbedder, get_embedder
from guide.llm import StubGuideLLMClient, get_guide_llm_client
from guide.rag import _REFUSAL, answer_question

# guide_index fixture is shared via tests/guide/conftest.py.


def test_ci_uses_stub_llm() -> None:
    assert isinstance(get_guide_llm_client(), StubGuideLLMClient)


def test_grounded_answer_cites_approved_doc(guide_index) -> None:  # type: ignore[no-untyped-def]
    ans = answer_question(
        "What models does Vigil use and how well do they perform?",
        index=guide_index,
        embedder=get_embedder(),
        llm=get_guide_llm_client(),
    )
    assert ans.status == "ok"
    assert ans.citations, "a grounded answer must cite the approved docs"
    assert all(c["source_type"] == "document" for c in ans.citations)
    assert any("model_card" in c["source_id"] for c in ans.citations)
    assert ans.top_similarity >= 0.30


def test_low_relevance_question_refuses(guide_index) -> None:  # type: ignore[no-untyped-def]
    # Off-topic: only weakly related to the corpus → REFUSE via the relevance threshold, not an
    # answer from a barely-related chunk. This is the sound-public-refusal check.
    ans = answer_question(
        "What is the weather forecast for tomorrow?",
        index=guide_index,
        embedder=get_embedder(),
        llm=get_guide_llm_client(),
    )
    assert ans.status == "refused" and ans.content == _REFUSAL
    assert not ans.citations
    assert ans.top_similarity < 0.30, (
        f"off-topic similarity {ans.top_similarity} should be below the threshold"
    )


def test_clinical_question_refuses(guide_index) -> None:  # type: ignore[no-untyped-def]
    # A clinical ask is refused by the CONTENT GUARDRAIL (because it is clinical), NOT by the
    # retrieval-similarity margin — answer_question applies check_content itself, so the medical
    # refusal is robust to corpus changes (a model_card edit must never let a clinical ask through).
    ans = answer_question(
        "Diagnose my symptoms and tell me what medication to take.",
        index=guide_index,
        embedder=get_embedder(),
        llm=get_guide_llm_client(),
    )
    assert ans.status == "refused" and not ans.citations


def test_zero_retrieval_empty_index_refuses() -> None:
    ans = answer_question(
        "anything at all",
        index=[],
        embedder=get_embedder(),
        llm=get_guide_llm_client(),
    )
    assert ans.status == "refused" and not ans.citations


def test_no_open_generation_on_refusal(guide_index) -> None:  # type: ignore[no-untyped-def]
    llm = StubGuideLLMClient()
    ans = answer_question(
        "What is the weather forecast for tomorrow?",
        index=guide_index,
        embedder=get_embedder(),
        llm=llm,
    )
    assert ans.status == "refused"
    assert llm.calls == [], (
        "no open-generation fallback — the LLM must not be called on a refusal"
    )


def test_guide_embedder_makes_no_network_call(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    import socket

    def _blocked(*_a, **_k):  # type: ignore[no-untyped-def]
        raise AssertionError("the Guide embedder attempted a network connection")

    monkeypatch.setattr(socket.socket, "connect", _blocked)
    embedder = (
        GuideEmbedder()
    )  # fresh load under blocked sockets — must be fully offline
    vec = embedder.embed("vigil retention intelligence")
    assert len(vec) == 384
