"""The Guide's OWN file-backed approved-doc index + retrieval (specs/isolation.md § file-backed).

The Guide's ONLY data source: a file-backed JSON index of embedded chunks from
`guide/approved_docs/`. NOT pgvector, NOT the app's `document_chunk`, NOT any shared datastore —
the Guide owns this file and reads only it. Retrieval is top-k cosine over the stored (already
L2-normalised) vectors. The Guide's entire toolset is this one search; there is no DB tool, no
structured source, no path to anything real.
"""

from __future__ import annotations

import json
import math
from dataclasses import dataclass
from pathlib import Path

# The Guide's ENTIRE toolset — exactly one capability (specs/isolation.md §1 tool-surface test).
# There is NO DB tool, NO structured-source tool, NO participant tool, NO path to anything
# tenant-scoped: the only way the Guide reaches grounding data is this file-backed vector search.
TOOLSET: tuple[str, ...] = ("approved_document_vector_search",)

# Chunking matches the app's card-corpus approach: paragraph blocks packed to a char bound.
_MAX_CHARS = 800


@dataclass(frozen=True, slots=True)
class IndexedChunk:
    source_ref: str
    chunk_index: int
    chunk_text: str
    embedding: list[float]


@dataclass(frozen=True, slots=True)
class Retrieved:
    source_ref: str
    chunk_index: int
    chunk_text: str
    similarity: float


def chunk_text(text: str, *, max_chars: int = _MAX_CHARS) -> list[str]:
    """Split on blank lines into paragraph blocks, packing up to ``max_chars`` per chunk."""
    paras = [p.strip() for p in text.split("\n\n") if p.strip()]
    chunks: list[str] = []
    buf = ""
    for para in paras:
        if buf and len(buf) + len(para) + 2 > max_chars:
            chunks.append(buf)
            buf = para
        else:
            buf = f"{buf}\n\n{para}" if buf else para
    if buf:
        chunks.append(buf)
    return chunks


def build_index(docs_dir: str | Path, out_path: str | Path, embedder) -> int:  # type: ignore[no-untyped-def]
    """Embed every ``*.md`` under ``docs_dir`` and write the file-backed index. Returns chunks.

    Deterministic: same docs + same embedder → same index. The Guide owns this output file.
    """
    docs = sorted(Path(docs_dir).glob("*.md"))
    records: list[dict] = []
    for path in docs:
        source_ref = path.name
        chunks = chunk_text(path.read_text(encoding="utf-8"))
        if not chunks:
            continue
        embeddings = embedder.embed_batch(chunks)
        for i, (chunk, emb) in enumerate(zip(chunks, embeddings, strict=True)):
            records.append(
                {
                    "source_ref": source_ref,
                    "chunk_index": i,
                    "chunk_text": chunk,
                    "embedding": emb,
                }
            )
    out = Path(out_path)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(
        json.dumps({"chunks": records}, ensure_ascii=False), encoding="utf-8"
    )
    return len(records)


def load_index(path: str | Path) -> list[IndexedChunk]:
    """Load the file-backed index; empty list if absent (→ the Guide refuses everything)."""
    p = Path(path)
    if not p.exists():
        return []
    data = json.loads(p.read_text(encoding="utf-8"))
    return [
        IndexedChunk(
            source_ref=r["source_ref"],
            chunk_index=int(r["chunk_index"]),
            chunk_text=r["chunk_text"],
            embedding=[float(x) for x in r["embedding"]],
        )
        for r in data.get("chunks", [])
    ]


def _cosine(a: list[float], b: list[float]) -> float:
    dot = sum(x * y for x, y in zip(a, b, strict=True))
    na = math.sqrt(sum(x * x for x in a))
    nb = math.sqrt(sum(y * y for y in b))
    if na == 0.0 or nb == 0.0:
        return 0.0
    return dot / (na * nb)


def search(
    index: list[IndexedChunk], query_embedding: list[float], *, top_k: int
) -> list[Retrieved]:
    """Top-k cosine search over the file-backed index (descending similarity)."""
    scored = [
        Retrieved(
            source_ref=c.source_ref,
            chunk_index=c.chunk_index,
            chunk_text=c.chunk_text,
            similarity=_cosine(query_embedding, c.embedding),
        )
        for c in index
    ]
    scored.sort(key=lambda r: r.similarity, reverse=True)
    return scored[:top_k]
