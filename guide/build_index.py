"""Build the Guide's OWN file-backed approved-doc index (build-time, Guide-side).

Embeds every `*.md` under the approved-docs dir with the Guide's offline embedder and writes the
file-backed index the Guide reads at runtime. Run locally / at image-build time:

    uv run python -m guide.build_index

Imports nothing from `vigil.*`. The output is a Guide-owned artifact (not the app's pgvector).
"""

from __future__ import annotations

from guide.config import get_config
from guide.embeddings import get_embedder
from guide.retrieval import build_index


def main() -> int:
    cfg = get_config()
    n = build_index(
        cfg.approved_docs_path, cfg.approved_docs_index_path, get_embedder()
    )
    print(
        f"Guide approved-doc index built: {n} chunks from {cfg.approved_docs_path} "
        f"-> {cfg.approved_docs_index_path}"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
