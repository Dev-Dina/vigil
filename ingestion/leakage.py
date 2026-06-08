"""Shared leakage primitives (ingestion-level, neutral).

The single source of truth for the group-disjointness invariant — one trial's rows never
span two folds. Both the Phase-1 synthetic per-participant leakage demo
(``ingestion.features``) and the Phase-3 temporal held-out split (``models.splits`` /
``models.leakage_check``) route through this, so Phase 3 imports *up* from ingestion and the
build layering is never inverted.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

from ingestion.errors import LeakageError


def assert_group_disjoint(folds: Mapping[str, Iterable[str]]) -> None:
    """Fail loud if any ``nct_id`` appears in more than one fold.

    ``folds`` maps a fold name (train/val/test) to its ``nct_id`` collection. Raises
    :class:`LeakageError` naming the two offending folds and a sample of the overlap.
    """
    sets = {name: set(ids) for name, ids in folds.items()}
    names = list(sets)
    for i in range(len(names)):
        for j in range(i + 1, len(names)):
            overlap = sets[names[i]] & sets[names[j]]
            if overlap:
                raise LeakageError(
                    f"nct_id(s) span two splits ({names[i]}, {names[j]}): "
                    f"{sorted(overlap)[:5]}"
                )
