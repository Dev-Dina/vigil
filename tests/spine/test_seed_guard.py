"""SEED-GUARD — re-running the seed on an already-seeded DB is a SAFE no-op.

The seed APPENDS with fresh UUIDs every run, so an unguarded second run doubles the cohort
(36 → 72). The guard turns that accident into an explicit refusal (``SeedExistsError``).

This test is deliberately NON-DESTRUCTIVE: it only exercises the guard's refusal path (which writes
nothing), so it never mutates the session-scoped ``migrated_db`` fixture other tests pin. The
empty-DB → seeds-normally path is proven by the fixture itself (it seeded a clean DB), and the
``--force`` wipe-and-reseed path is proven on a throwaway DB (see the gate report).
"""

from __future__ import annotations

import pytest

from vigil.repositories.session import platform_session
from vigil.seed import (
    SeedExistsError,
    _count_seeded_participants,
    _existing_seed_sponsors,
    seed,
)


def _seeded_participant_count() -> int:
    with platform_session() as session:
        sponsors = _existing_seed_sponsors(session)
    return _count_seeded_participants([sp.id for sp in sponsors])


def test_reseed_on_seeded_db_refuses_and_appends_nothing(
    migrated_db: dict[str, str],
) -> None:
    # The fixture already seeded a CLEAN DB → the demo sponsors + cohort exist (empty-DB path works).
    before = _seeded_participant_count()
    assert before >= 36, f"fixture seed should have >=36 participants, got {before}"

    # A second seed() (no force) must REFUSE — never append a second cohort.
    with pytest.raises(SeedExistsError) as excinfo:
        seed()
    assert excinfo.value.n_participants == before

    after = _seeded_participant_count()
    assert after == before, (
        f"guarded reseed must append nothing: {before} → {after} (the doubling bug)"
    )
