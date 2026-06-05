"""Controlled vocabularies and normalisation maps for the clean stage.

Unmapped originals are never silently lost: callers record them in the quality report and
fall back to ``OTHER``.
"""

from __future__ import annotations

# --- Withdrawal reason controlled vocabulary (specs/data.md) --------------------------
WITHDRAWAL_REASONS = (
    "ADVERSE_EVENT",
    "LACK_OF_EFFICACY",
    "WITHDRAWAL_BY_SUBJECT",
    "LOST_TO_FOLLOWUP",
    "PHYSICIAN_DECISION",
    "PROTOCOL_VIOLATION",
    "DEATH",
    "PREGNANCY",
    "NONCOMPLIANCE",
    "OTHER",
)

# Substring rules over the lowercased free-text reason. First match wins; order matters
# (more specific before generic). Anything unmatched -> OTHER, original recorded.
_REASON_RULES: tuple[tuple[str, str], ...] = (
    ("adverse event", "ADVERSE_EVENT"),
    ("adverse reaction", "ADVERSE_EVENT"),
    ("side effect", "ADVERSE_EVENT"),
    ("toxicity", "ADVERSE_EVENT"),
    ("lack of efficacy", "LACK_OF_EFFICACY"),
    ("lack of effect", "LACK_OF_EFFICACY"),
    ("disease progression", "LACK_OF_EFFICACY"),
    ("progressive disease", "LACK_OF_EFFICACY"),
    ("withdrawal by subject", "WITHDRAWAL_BY_SUBJECT"),
    ("withdrawal by patient", "WITHDRAWAL_BY_SUBJECT"),
    ("subject withdrew", "WITHDRAWAL_BY_SUBJECT"),
    ("patient withdrew", "WITHDRAWAL_BY_SUBJECT"),
    ("withdrew consent", "WITHDRAWAL_BY_SUBJECT"),
    ("consent withdrawn", "WITHDRAWAL_BY_SUBJECT"),
    ("voluntary withdrawal", "WITHDRAWAL_BY_SUBJECT"),
    ("lost to follow", "LOST_TO_FOLLOWUP"),
    ("lost to fu", "LOST_TO_FOLLOWUP"),
    ("physician decision", "PHYSICIAN_DECISION"),
    ("investigator decision", "PHYSICIAN_DECISION"),
    ("physician's decision", "PHYSICIAN_DECISION"),
    ("protocol violation", "PROTOCOL_VIOLATION"),
    ("protocol deviation", "PROTOCOL_VIOLATION"),
    ("eligibility", "PROTOCOL_VIOLATION"),
    ("death", "DEATH"),
    ("died", "DEATH"),
    ("deceased", "DEATH"),
    ("pregnan", "PREGNANCY"),
    ("noncompliance", "NONCOMPLIANCE"),
    ("non-compliance", "NONCOMPLIANCE"),
    ("non compliance", "NONCOMPLIANCE"),
    ("compliance", "NONCOMPLIANCE"),
)


def normalize_reason(raw: str) -> tuple[str, str | None]:
    """Map a free-text withdrawal reason to the controlled vocab.

    Returns ``(canonical, unmapped_original)`` where ``unmapped_original`` is the original
    text when it fell through to ``OTHER`` (so the caller can record it), else ``None``.
    """
    text = (raw or "").strip().lower()
    if not text:
        return "OTHER", raw
    for needle, canonical in _REASON_RULES:
        if needle in text:
            return canonical, None
    return "OTHER", raw


# --- MeSH term -> therapeutic area -----------------------------------------------------
# Substring rules over the lowercased MeSH term. Unmapped -> OTHER, original recorded.
THERAPEUTIC_AREAS = (
    "ONCOLOGY",
    "CARDIOVASCULAR",
    "NEUROLOGY",
    "PSYCHIATRY",
    "INFECTIOUS_DISEASE",
    "ENDOCRINE_METABOLIC",
    "RESPIRATORY",
    "IMMUNOLOGY_RHEUMATOLOGY",
    "GASTROENTEROLOGY",
    "RENAL_UROLOGY",
    "DERMATOLOGY",
    "OPHTHALMOLOGY",
    "MUSCULOSKELETAL",
    "OTHER",
)

_MESH_RULES: tuple[tuple[str, str], ...] = (
    ("neoplasm", "ONCOLOGY"),
    ("cancer", "ONCOLOGY"),
    ("carcinoma", "ONCOLOGY"),
    ("tumor", "ONCOLOGY"),
    ("lymphoma", "ONCOLOGY"),
    ("leukemia", "ONCOLOGY"),
    ("melanoma", "ONCOLOGY"),
    ("sarcoma", "ONCOLOGY"),
    ("cardiac", "CARDIOVASCULAR"),
    ("cardiovascular", "CARDIOVASCULAR"),
    ("heart", "CARDIOVASCULAR"),
    ("hypertension", "CARDIOVASCULAR"),
    ("coronary", "CARDIOVASCULAR"),
    ("stroke", "NEUROLOGY"),
    ("alzheimer", "NEUROLOGY"),
    ("parkinson", "NEUROLOGY"),
    ("epilep", "NEUROLOGY"),
    ("multiple sclerosis", "NEUROLOGY"),
    ("migraine", "NEUROLOGY"),
    ("neuro", "NEUROLOGY"),
    ("depress", "PSYCHIATRY"),
    ("anxiety", "PSYCHIATRY"),
    ("schizophren", "PSYCHIATRY"),
    ("bipolar", "PSYCHIATRY"),
    ("mental disorder", "PSYCHIATRY"),
    ("psychiatr", "PSYCHIATRY"),
    ("infection", "INFECTIOUS_DISEASE"),
    ("hiv", "INFECTIOUS_DISEASE"),
    ("hepatitis", "INFECTIOUS_DISEASE"),
    ("influenza", "INFECTIOUS_DISEASE"),
    ("covid", "INFECTIOUS_DISEASE"),
    ("tuberculosis", "INFECTIOUS_DISEASE"),
    ("bacterial", "INFECTIOUS_DISEASE"),
    ("viral", "INFECTIOUS_DISEASE"),
    ("diabetes", "ENDOCRINE_METABOLIC"),
    ("obesity", "ENDOCRINE_METABOLIC"),
    ("thyroid", "ENDOCRINE_METABOLIC"),
    ("metabolic", "ENDOCRINE_METABOLIC"),
    ("cholesterol", "ENDOCRINE_METABOLIC"),
    ("lipid", "ENDOCRINE_METABOLIC"),
    ("asthma", "RESPIRATORY"),
    ("copd", "RESPIRATORY"),
    ("pulmonary", "RESPIRATORY"),
    ("respiratory", "RESPIRATORY"),
    ("lung disease", "RESPIRATORY"),
    ("arthritis", "IMMUNOLOGY_RHEUMATOLOGY"),
    ("lupus", "IMMUNOLOGY_RHEUMATOLOGY"),
    ("rheumat", "IMMUNOLOGY_RHEUMATOLOGY"),
    ("autoimmune", "IMMUNOLOGY_RHEUMATOLOGY"),
    ("psoriasis", "DERMATOLOGY"),
    ("dermat", "DERMATOLOGY"),
    ("skin", "DERMATOLOGY"),
    ("bowel", "GASTROENTEROLOGY"),
    ("crohn", "GASTROENTEROLOGY"),
    ("colitis", "GASTROENTEROLOGY"),
    ("gastro", "GASTROENTEROLOGY"),
    ("hepatic", "GASTROENTEROLOGY"),
    ("liver", "GASTROENTEROLOGY"),
    ("renal", "RENAL_UROLOGY"),
    ("kidney", "RENAL_UROLOGY"),
    ("urinary", "RENAL_UROLOGY"),
    ("bladder", "RENAL_UROLOGY"),
    ("retina", "OPHTHALMOLOGY"),
    ("glaucoma", "OPHTHALMOLOGY"),
    ("macular", "OPHTHALMOLOGY"),
    ("ocular", "OPHTHALMOLOGY"),
    ("eye", "OPHTHALMOLOGY"),
    ("osteoarthritis", "MUSCULOSKELETAL"),
    ("osteoporosis", "MUSCULOSKELETAL"),
    ("bone", "MUSCULOSKELETAL"),
    ("muscle", "MUSCULOSKELETAL"),
    ("musculoskeletal", "MUSCULOSKELETAL"),
)


def map_therapeutic_area(mesh_term: str) -> tuple[str, str | None]:
    """Map a MeSH term (or condition name) to a therapeutic area.

    Returns ``(area, unmapped_original)``; ``unmapped_original`` is set when it fell to
    ``OTHER`` so the caller records it.
    """
    text = (mesh_term or "").strip().lower()
    if not text:
        return "OTHER", mesh_term
    for needle, area in _MESH_RULES:
        if needle in text:
            return area, None
    return "OTHER", mesh_term
