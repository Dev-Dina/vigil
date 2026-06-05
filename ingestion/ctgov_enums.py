"""CTGOV2 -> spec enum normalisation for the AACT clean stage.

The hosted AACT snapshot we capture from serves ClinicalTrials.gov's **CTGOV2** enum codes,
which are uppercased machine tokens (``INTERVENTIONAL``, ``PHASE2``, ``RANDOMIZED``,
``ALL`` ...). As ratified in ``specs/data.md`` ("Enum format (CTGOV2)"), those CTGOV2 codes
**are** the canonical values stored in ``ref_*`` and pinned by :mod:`ingestion.schema`; the
former legacy human-readable strings are no longer used.

These maps therefore normalise a raw token onto the spec's controlled CTGOV2 set — now
mostly an identity pass-through. They remain the single normalisation seam (the same pattern
as the sponsor-class / arm-type / MeSH / reason maps), NOT a loosening of validation: the
Pydantic enums stay the single source of truth, and any token without a spec category here
returns ``None`` so the clean stage fails loud and records it in the data-quality report. A
token genuinely outside the spec's controlled set is surfaced, never silently coerced.

The earlier casing contradiction is now ratified (the spec adopts the CTGOV2 format), so it
is no longer an outstanding spec contradiction.
"""

from __future__ import annotations

# study_type — spec post-filter is INTERVENTIONAL only.
STUDY_TYPE: dict[str, str] = {
    "INTERVENTIONAL": "INTERVENTIONAL",
}

# phase — spec Phase class values (CTGOV2 tokens).
PHASE: dict[str, str] = {
    "EARLY_PHASE1": "EARLY_PHASE1",
    "PHASE1": "PHASE1",
    "PHASE1/PHASE2": "PHASE1/PHASE2",
    "PHASE2": "PHASE2",
    "PHASE2/PHASE3": "PHASE2/PHASE3",
    "PHASE3": "PHASE3",
    "PHASE4": "PHASE4",
    "NA": "NA",
}

ALLOCATION: dict[str, str] = {
    "RANDOMIZED": "RANDOMIZED",
    "NON_RANDOMIZED": "NON_RANDOMIZED",
    "NA": "NA",
}

INTERVENTION_MODEL: dict[str, str] = {
    "PARALLEL": "PARALLEL",
    "CROSSOVER": "CROSSOVER",
    "SINGLE_GROUP": "SINGLE_GROUP",
    "FACTORIAL": "FACTORIAL",
    "SEQUENTIAL": "SEQUENTIAL",
}

MASKING: dict[str, str] = {
    "NONE": "NONE",
    "SINGLE": "SINGLE",
    "DOUBLE": "DOUBLE",
    "TRIPLE": "TRIPLE",
    "QUADRUPLE": "QUADRUPLE",
}

# Spec PrimaryPurpose set (CTGOV2). The ratified spec now INCLUDES "ECT"
# (electroconvulsive therapy as purpose), so it maps through to a spec category. Any token
# absent here (genuinely outside the spec's set) still resolves to None and fails loud.
PRIMARY_PURPOSE: dict[str, str] = {
    "TREATMENT": "TREATMENT",
    "PREVENTION": "PREVENTION",
    "DIAGNOSTIC": "DIAGNOSTIC",
    "SUPPORTIVE_CARE": "SUPPORTIVE_CARE",
    "SCREENING": "SCREENING",
    "HEALTH_SERVICES_RESEARCH": "HEALTH_SERVICES_RESEARCH",
    "BASIC_SCIENCE": "BASIC_SCIENCE",
    "DEVICE_FEASIBILITY": "DEVICE_FEASIBILITY",
    "ECT": "ECT",
    "OTHER": "OTHER",
}

GENDER: dict[str, str] = {
    "ALL": "ALL",
    "FEMALE": "FEMALE",
    "MALE": "MALE",
}


def normalize_enum(
    table_map: dict[str, str], raw: str | None, *, na_value: str | None = None
) -> tuple[str | None, str | None]:
    """Map a raw CTGOV2 token onto the spec's enum value.

    Returns ``(canonical, unmapped_original)``. ``canonical`` is ``None`` when the token is
    missing or has no spec category, in which case ``unmapped_original`` carries the offending
    raw value so the caller can fail loud / record it. ``na_value`` lets a column treat a
    missing token as a defined "N/A" spec value (e.g. ``designs`` columns), matching the
    pre-existing clean-stage behaviour of defaulting empty design fields to ``N/A``.

    A value that is ALREADY a spec-canonical string (e.g. the committed sample fixture, which
    stores ``'Phase 2'`` directly) passes through unchanged, so both the legacy-format fixture
    and the CTGOV2 hosted snapshot clean identically.
    """
    if raw is None or str(raw).strip() == "":
        return (na_value, None) if na_value is not None else (None, "")
    raw_str = str(raw).strip()
    if raw_str in table_map.values():  # already a spec-canonical value
        return raw_str, None
    token = raw_str.upper()
    if token in table_map:
        return table_map[token], None
    return None, raw_str
