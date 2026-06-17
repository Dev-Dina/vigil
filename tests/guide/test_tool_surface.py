"""Gate 7.4 LAYER 1.3 — TOOL-SURFACE: the Guide's toolset is EXACTLY {vector search}.

specs/isolation.md §1 tool-surface: the Guide agent's registered toolset must be exactly
{approved-document vector search} — no DB tool, no structured-source tool, no participant tool, no
path to anything tenant-scoped. Proven structurally: the declared toolset is the single vector
search, the retrieval module that backs it is PURE (no network/DB capability whatsoever), and the
grounding path (`guide.rag`) reaches data ONLY through `guide.retrieval` (no http/db client).
"""

from __future__ import annotations

import ast
from pathlib import Path

import guide.rag as rag_mod
import guide.retrieval as retrieval_mod

_REPO_ROOT = Path(__file__).resolve().parents[2]

# Modules the retrieval (the one grounding tool) may use — all PURE stdlib; nothing that can open
# a socket or a DB connection. The presence of any network/DB client here would be a second tool.
_RETRIEVAL_ALLOWED_ROOTS = {
    "__future__",
    "json",
    "math",
    "dataclasses",
    "typing",
    "pathlib",
}

# The grounding path (rag) may reach data ONLY through guide.* — never a network/DB client.
_RAG_ALLOWED_ROOTS = {"__future__", "dataclasses", "guide"}


def _import_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            roots.update(a.name.split(".")[0] for a in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            roots.add(node.module.split(".")[0])
    return roots


def test_declared_toolset_is_exactly_vector_search() -> None:
    assert retrieval_mod.TOOLSET == ("approved_document_vector_search",), (
        f"the Guide toolset must be exactly the vector search; got {retrieval_mod.TOOLSET}"
    )


def test_retrieval_tool_is_pure_no_network_or_db_capability() -> None:
    roots = _import_roots(Path(retrieval_mod.__file__))
    extra = roots - _RETRIEVAL_ALLOWED_ROOTS
    assert not extra, (
        f"the vector-search tool must be pure (no socket/DB/HTTP capability); unexpected: {extra}"
    )
    # belt-and-suspenders: none of the network/DB client names appear in the source
    src = Path(retrieval_mod.__file__).read_text(encoding="utf-8")
    for forbidden in ("socket", "httpx", "requests", "urllib", "sqlalchemy", "psycopg"):
        assert forbidden not in src, f"retrieval tool must not reference {forbidden!r}"


def test_grounding_path_reaches_data_only_through_retrieval() -> None:
    roots = _import_roots(Path(rag_mod.__file__))
    extra = roots - _RAG_ALLOWED_ROOTS
    assert not extra, (
        f"the grounding path (rag) must reach data only via guide.*; unexpected roots: {extra}"
    )
