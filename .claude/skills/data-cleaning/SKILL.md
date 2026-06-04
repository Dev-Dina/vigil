---
name: data-cleaning
description: Use when ingesting or cleaning ClinicalTrials.gov/AACT data or generating the synthetic cohort for Vigil. Encodes the data spec's validation rules, the cleaned schema, and the fail-loud principle. Trigger for any data pipeline, transform, validation, or synthetic-cohort work.
---

# Data cleaning & synthesis (Vigil)

Authoritative contract: `/specs/data.md`. If this skill and the spec disagree, the spec wins —
update the spec first, then the code.

## Rules
- ClinicalTrials.gov/AACT is **build-time ingestion only**. Never call it at request time.
  Save raw JSON under `data/raw/` and never re-fetch needlessly.
- Validation **fails loud**: a missing or malformed field raises a typed error. Never coerce
  to a plausible default, never silently drop rows without recording it in the quality report.
- The synthetic cohort is **clearly labelled** (a `synthetic=True` flag on every row) and
  **calibrated** to the real aggregate statistics named in the spec. Use a deterministic seed.
- Separate static features from time-varying features; the DL signal is *change over time*.
- Emit a data-quality report (row counts, null rates, validation failures) as a pipeline output.

## Steps for any data task
1. Read `/specs/data.md` and confirm the field list / schema you are implementing against.
2. Write pure, typed functions; validate at the boundary with Pydantic.
3. Make the pipeline reproducible from one command; pin seeds.
4. Update the data-quality report.
