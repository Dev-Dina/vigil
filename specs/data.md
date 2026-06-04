# Data Spec

## Decisions (fixed)
- Source: ClinicalTrials.gov / AACT — **build-time ingestion only**, never live, never agent-reached.
- Real aggregate outcomes are modelled directly; those statistics calibrate a SYNTHETIC
  per-participant cohort (clearly labelled) for the deep-learning layer.
- Claim is method validity / partner-readiness, never clinical prediction. No PHI.

## Raw ingestion
TODO: list AACT/ClinicalTrials.gov fields pulled; pagination; raw JSON layout under data/raw/.

## Cleaned schema
TODO: define cleaned tables + types. Validation fails LOUD on missing/malformed fields.

## Synthetic cohort
TODO: generation rules; the real statistics it must match; deterministic seed; the synthetic flag.

## Features
- Static: age, baseline severity, socioeconomic proxy, travel friction, prior-trial experience, comorbidities.
- Time-varying (the DL signal): diary-completion rate + trend, days since last entry,
  reminder-response latency, app-opens, missed visits, symptom-log frequency. Signal = change over time.
TODO: exact feature definitions, windows, and leakage rules.

## Done when
One command runs raw -> clean -> synthetic, emits a data-quality report, validates against this spec.
