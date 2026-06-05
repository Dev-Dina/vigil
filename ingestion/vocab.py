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
    "STUDY_TERMINATED",
    "ADMINISTRATIVE",
    "OTHER",
)

# Substring rules over the lowercased free-text reason. First match wins; order matters
# (more specific before generic). Anything unmatched -> OTHER, original recorded.
#
# Rules are mapped honestly: a term is only assigned a category it genuinely belongs to.
# Sponsor/DSMB-driven study termination -> STUDY_TERMINATED and survey/data-collection
# bookkeeping -> ADMINISTRATIVE are real exits in the flow counts but are NOT participant-
# level clinical retention events; they are kept distinct from the clinical reasons.
# Censoring / still-ongoing terms ("entered open-label period", "ongoing", "rolled over")
# are NOT a dropout reason at all: ``normalize_reason`` returns ``("__CENSORED__", raw)`` for
# them so the caller excludes the row from the reason-mix entirely (recorded separately as
# excluded-censoring), never mapped to a category nor to OTHER. Genuinely unspecified terms
# ("other", "reason not specified", disease-specific exclusions) remain OTHER on purpose.
_REASON_RULES: tuple[tuple[str, str], ...] = (
    # --- ADVERSE_EVENT ---
    ("adverse event", "ADVERSE_EVENT"),
    ("adverse reaction", "ADVERSE_EVENT"),
    ("side effect", "ADVERSE_EVENT"),
    ("toxicity", "ADVERSE_EVENT"),
    # --- DEATH (before generic disease/progression so a fatal outcome wins) ---
    ("death", "DEATH"),
    ("died", "DEATH"),
    ("deceased", "DEATH"),
    ("fatal", "DEATH"),
    # --- LACK_OF_EFFICACY (therapeutic failure / clinical worsening) ---
    ("lack of efficacy", "LACK_OF_EFFICACY"),
    ("lack of effect", "LACK_OF_EFFICACY"),
    ("lack of therapeutic", "LACK_OF_EFFICACY"),
    ("disease progression", "LACK_OF_EFFICACY"),
    ("progressive disease", "LACK_OF_EFFICACY"),
    ("progression of disease", "LACK_OF_EFFICACY"),
    ("disease progressed", "LACK_OF_EFFICACY"),
    ("clinical progression", "LACK_OF_EFFICACY"),
    ("clinical deterioration", "LACK_OF_EFFICACY"),
    ("condition under investigation worsened", "LACK_OF_EFFICACY"),
    ("treatment failure", "LACK_OF_EFFICACY"),
    ("therapeutic failure", "LACK_OF_EFFICACY"),
    ("unsatisfactory therapeutic effect", "LACK_OF_EFFICACY"),
    ("insufficient therapeutic response", "LACK_OF_EFFICACY"),
    ("inadequate therapeutic response", "LACK_OF_EFFICACY"),
    ("relapse", "LACK_OF_EFFICACY"),
    ("progression/relapse", "LACK_OF_EFFICACY"),
    ("progression or relapse", "LACK_OF_EFFICACY"),
    # --- WITHDRAWAL_BY_SUBJECT (participant/proxy-initiated departure) ---
    ("withdrawal by subject", "WITHDRAWAL_BY_SUBJECT"),
    ("withdrawal by patient", "WITHDRAWAL_BY_SUBJECT"),
    ("withdrawal by parent", "WITHDRAWAL_BY_SUBJECT"),
    ("withdrawal by guardian", "WITHDRAWAL_BY_SUBJECT"),
    ("subject withdrew", "WITHDRAWAL_BY_SUBJECT"),
    ("patient withdrew", "WITHDRAWAL_BY_SUBJECT"),
    ("withdrew consent", "WITHDRAWAL_BY_SUBJECT"),
    ("withdrawal of consent", "WITHDRAWAL_BY_SUBJECT"),
    ("consent withdrawn", "WITHDRAWAL_BY_SUBJECT"),
    ("consent withdrawal", "WITHDRAWAL_BY_SUBJECT"),
    ("voluntary withdrawal", "WITHDRAWAL_BY_SUBJECT"),
    ("declined follow-up", "WITHDRAWAL_BY_SUBJECT"),
    ("did not consent", "WITHDRAWAL_BY_SUBJECT"),
    ("declined to participate", "WITHDRAWAL_BY_SUBJECT"),
    ("no longer willing to participate", "WITHDRAWAL_BY_SUBJECT"),
    ("refused to participate", "WITHDRAWAL_BY_SUBJECT"),
    ("refused further participation", "WITHDRAWAL_BY_SUBJECT"),
    ("subject decision", "WITHDRAWAL_BY_SUBJECT"),
    ("subject/guardian decision", "WITHDRAWAL_BY_SUBJECT"),
    ("participant decision", "WITHDRAWAL_BY_SUBJECT"),
    ("decision by participant", "WITHDRAWAL_BY_SUBJECT"),
    ("patient decision", "WITHDRAWAL_BY_SUBJECT"),
    ("patient request", "WITHDRAWAL_BY_SUBJECT"),
    ("subject request", "WITHDRAWAL_BY_SUBJECT"),
    ("withdrawal of participant", "WITHDRAWAL_BY_SUBJECT"),
    # --- LOST_TO_FOLLOWUP ---
    ("lost to follow", "LOST_TO_FOLLOWUP"),
    ("lost to fu", "LOST_TO_FOLLOWUP"),
    # --- PHYSICIAN_DECISION ---
    ("physician decision", "PHYSICIAN_DECISION"),
    ("physician's decision", "PHYSICIAN_DECISION"),
    ("physician refused", "PHYSICIAN_DECISION"),
    ("investigator decision", "PHYSICIAN_DECISION"),
    ("investigator's decision", "PHYSICIAN_DECISION"),
    ("clinician decision", "PHYSICIAN_DECISION"),
    # --- PREGNANCY ---
    ("pregnan", "PREGNANCY"),
    # --- PROTOCOL_VIOLATION (eligibility / inclusion-exclusion / protocol breaches) ---
    ("protocol violation", "PROTOCOL_VIOLATION"),
    ("protocol deviation", "PROTOCOL_VIOLATION"),
    ("protocol-specified criteria", "PROTOCOL_VIOLATION"),
    ("protocol specified withdrawal", "PROTOCOL_VIOLATION"),
    ("protocol specified criterion", "PROTOCOL_VIOLATION"),
    ("withdrawal criteria", "PROTOCOL_VIOLATION"),
    ("withdrawal criterion", "PROTOCOL_VIOLATION"),
    ("eligibility", "PROTOCOL_VIOLATION"),
    ("ineligible", "PROTOCOL_VIOLATION"),
    ("not eligible", "PROTOCOL_VIOLATION"),
    ("were not eligible", "PROTOCOL_VIOLATION"),
    ("screen failure", "PROTOCOL_VIOLATION"),
    ("screening failure", "PROTOCOL_VIOLATION"),
    ("did not meet inclusion", "PROTOCOL_VIOLATION"),
    ("did not meet eligibility", "PROTOCOL_VIOLATION"),
    ("did not meet the inclusion", "PROTOCOL_VIOLATION"),
    ("inclusion criteria not met", "PROTOCOL_VIOLATION"),
    ("entry criteria not met", "PROTOCOL_VIOLATION"),
    ("met exclusion", "PROTOCOL_VIOLATION"),
    ("exclusion criteria", "PROTOCOL_VIOLATION"),
    ("randomized in error", "PROTOCOL_VIOLATION"),
    ("randomised in error", "PROTOCOL_VIOLATION"),
    ("randomized but not treated", "PROTOCOL_VIOLATION"),
    ("randomised but not treated", "PROTOCOL_VIOLATION"),
    # --- NONCOMPLIANCE ---
    ("noncompliance", "NONCOMPLIANCE"),
    ("non-compliance", "NONCOMPLIANCE"),
    ("non compliance", "NONCOMPLIANCE"),
    ("noncompliant", "NONCOMPLIANCE"),
    ("non-compliant", "NONCOMPLIANCE"),
    ("compliance", "NONCOMPLIANCE"),
    # --- STUDY_TERMINATED (sponsor/DSMB-driven study or arm closure; not participant-level).
    #     Placed after the clinical reasons so a real AE/death/efficacy reason still wins. ---
    ("terminated by sponsor", "STUDY_TERMINATED"),
    ("terminated by the sponsor", "STUDY_TERMINATED"),
    ("study terminated", "STUDY_TERMINATED"),
    ("study termination", "STUDY_TERMINATED"),
    ("trial terminated", "STUDY_TERMINATED"),
    ("trial was terminated", "STUDY_TERMINATED"),
    ("study was terminated", "STUDY_TERMINATED"),
    ("sponsor terminated", "STUDY_TERMINATED"),
    ("sponsor termination", "STUDY_TERMINATED"),
    ("sponsor study termination", "STUDY_TERMINATED"),
    ("termination of study", "STUDY_TERMINATED"),
    ("terminated study", "STUDY_TERMINATED"),
    ("study closed", "STUDY_TERMINATED"),
    ("closed/terminated", "STUDY_TERMINATED"),
    ("study halted", "STUDY_TERMINATED"),
    ("study stopped", "STUDY_TERMINATED"),
    ("study was stopped", "STUDY_TERMINATED"),
    ("study was halted", "STUDY_TERMINATED"),
    ("dsmb", "STUDY_TERMINATED"),
    ("data monitoring committee", "STUDY_TERMINATED"),
    ("data safety monitoring", "STUDY_TERMINATED"),
    ("arm closed", "STUDY_TERMINATED"),
    ("arm discontinued", "STUDY_TERMINATED"),
    ("arm was closed", "STUDY_TERMINATED"),
    ("sponsor decision", "STUDY_TERMINATED"),
    ("sponsor's decision", "STUDY_TERMINATED"),
    ("decision to terminate", "STUDY_TERMINATED"),
    ("sponsor discontinued study", "STUDY_TERMINATED"),
    ("study discontinued by sponsor", "STUDY_TERMINATED"),
    ("discontinued by sponsor", "STUDY_TERMINATED"),
    ("discontinuation by sponsor", "STUDY_TERMINATED"),
    ("discontinuation of study by sponsor", "STUDY_TERMINATED"),
    ("sponsor request", "STUDY_TERMINATED"),
    # --- ADMINISTRATIVE (survey/data-collection bookkeeping; no clinical retention meaning).
    #     "compliance" above is NONCOMPLIANCE; these are data-completeness, not noncompliance. ---
    ("did not complete any study assessment", "ADMINISTRATIVE"),
    ("did not complete mid-study", "ADMINISTRATIVE"),
    ("did not complete the 3 or 6 month", "ADMINISTRATIVE"),
    ("did not complete final survey", "ADMINISTRATIVE"),
    ("did not have a post-assessment", "ADMINISTRATIVE"),
    ("did not answer at least two surveys", "ADMINISTRATIVE"),
    ("survey", "ADMINISTRATIVE"),
    ("did not post", "ADMINISTRATIVE"),
    ("did not return fit", "ADMINISTRATIVE"),
    ("withdrawal information not recorded", "ADMINISTRATIVE"),
    ("data incomplete", "ADMINISTRATIVE"),
    ("data not available", "ADMINISTRATIVE"),
    ("data not collected", "ADMINISTRATIVE"),
    ("missing data", "ADMINISTRATIVE"),
    ("case report form", "ADMINISTRATIVE"),
    ("crf", "ADMINISTRATIVE"),
    ("administrative", "ADMINISTRATIVE"),
    ("duplicate record", "ADMINISTRATIVE"),
    ("not part of the random sample", "ADMINISTRATIVE"),
    ("not included in the analytic cohort", "ADMINISTRATIVE"),
    ("non-response", "ADMINISTRATIVE"),
    # --- generic participant refusal (kept LAST so the specific physician/eligibility/AE
    #     rules above win first). A bare "refused"/"refusal" by a participant is a
    #     subject-initiated departure. Institutional refusals (site/nurse/sponsor/insurance/
    #     anesthesia/obstetrician) are NOT subject decisions — see _INSTITUTIONAL_REFUSAL,
    #     which keeps them OTHER. ---
    ("refus", "WITHDRAWAL_BY_SUBJECT"),
)

# Refusal phrases that are institutional, not participant-initiated: they must NOT be mapped
# to WITHDRAWAL_BY_SUBJECT by the generic "refus" rule. Kept OTHER (original recorded).
_INSTITUTIONAL_REFUSAL: tuple[str, ...] = (
    "site refusal",
    "site refused",
    "nurse refusal",
    "nurse refused",
    "sponsor refus",
    "insurance refus",
    "insurance company refused",
    "obstetrician refusal",
    "anesthesia provider refusal",
    "rehab refused",
    "investigator refused to log",
)

# Sentinel returned by ``normalize_reason`` for right-censoring / still-ongoing terms. Per
# specs/data.md these are NOT a dropout reason: the caller excludes the row from
# ``ref_withdrawal_reason`` entirely (neither a category nor OTHER) and records it separately
# as excluded-censoring. Never count toward explained dropout / the reason-mix.
CENSORED = "__CENSORED__"

# Explicit censoring / continuation rule list (recognised, never guessed). A term here denotes
# right-censoring or rollover/continuation to a later study phase, not a participant exit.
_CENSORING_TERMS: tuple[str, ...] = (
    "entered open label",
    "entered open-label",
    "open label period",
    "open-label period",
    "switch to open label",
    "switched to open label",
    "switch to open-label",
    "switched to open-label",
    "moved to open-label",
    "transfer to open-label",
    "open-label phase",
    "open-label extension",
    "open-label study",
    "still on study",
    "still on treatment",
    "on study treatment",
    "ongoing",
    "rolled over",
    "roll over",
    "roll-over",
    "rollover",
    "continuation criteria",
    "did not continue into extension",
    "did not continue in extension",
    "entered extension",
    "extension study",
    "into extension",
    "in extension",
    "long term extension",
    "long-term extension",
    "continuing in randomized",
    "participants ongoing",
    "study still ongoing",
    "remain on study",
    "remains on study",
    "censored",
)


def normalize_reason(raw: str) -> tuple[str, str | None]:
    """Map a free-text withdrawal reason to the controlled vocab.

    Returns ``(canonical, unmapped_original)``:

    - ``(CENSORED, raw)`` for right-censoring / still-ongoing terms — the caller drops the row
      from ``ref_withdrawal_reason`` and records it as excluded-censoring (never a category,
      never OTHER, never counted toward dropout).
    - ``(canonical, None)`` for a mapped controlled-vocab reason.
    - ``("OTHER", raw)`` when nothing mapped, so the caller records the original (no silent loss).
    """
    text = (raw or "").strip().lower()
    if not text:
        return "OTHER", raw
    # Censoring / continuation is recognised by an explicit rule list (not guessed) and is
    # excluded from the reason-mix entirely. Checked first so it can never be miscategorised.
    if any(token in text for token in _CENSORING_TERMS):
        return CENSORED, raw
    # Institutional refusals are not participant decisions: keep them OTHER (original logged)
    # rather than letting the generic "refus" rule mislabel them WITHDRAWAL_BY_SUBJECT.
    if any(token in text for token in _INSTITUTIONAL_REFUSAL):
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
