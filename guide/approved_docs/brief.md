# Vigil — Project Brief

Vigil is a **clinical-trial retention-intelligence** platform. It **surfaces and explains**
participant dropout **risk** early — ranking the cohort so staff can triage the highest-risk
participants first and **explaining every flag** (which factors drove a risk score) — so a human
can act on it rather than trust a black box. It **assists retention**; it does not claim to predict
who will drop out.

## The problem
Participant dropout is one of the most expensive and least predictable risks in a clinical trial.
Lost participants reduce statistical power, delay readouts, and can compromise a study. Sites
usually notice disengagement late. Vigil's goal is to surface that risk **earlier and with a
reason attached**, so a coordinator can reach out before a participant is lost.

## What Vigil does
- **Surfaces and estimates** per-participant dropout **risk** and assigns a risk band (high / medium / low).
- **Ranks** the cohort for triage — the riskiest participants rise to the top of the worklist.
- **Explains** each flag with the contributing factors behind the score.
- **Audits** every prediction and assistant answer so a reviewer can trace what happened.

## Two surfaces (kept strictly separate)
1. **The operational app** — the real, authenticated product used by trial staff: dashboards,
   the ranked cohort, participant detail, the in-app assistant, monitoring and cost views. Access
   is role-scoped and tenant-isolated.
2. **This public Guide** — an isolated, guest-facing chatbot (what you are talking to). It
   explains the project from a small set of approved public documents and **nothing else**. It
   has no connection to the real app, no access to any participant data, and no ability to take
   any action. See the Safety Policy for exactly what it can and cannot do.

## Honest scope (what Vigil is and is not)
- Vigil explains **retention risk and the method behind it**. It is **not** a medical device and
  gives **no** clinical, diagnostic, or treatment advice.
- The deep-learning trajectory results in this portfolio were produced on a **clearly-labelled
  synthetic cohort** calibrated to real published trial statistics. They demonstrate that the
  **method and architecture** work; they are **not** a clinical prediction and were **not**
  validated on real individual-participant data. See the Model Card for the honest numbers and
  their limits.
- The registry-scale analysis uses **real, public** ClinicalTrials.gov / AACT trial-level data —
  aggregate, no patient-level data, no protected health information (PHI).

## Who it is for
Sponsors, Clinical Research Organizations (CROs), and site staff (coordinators, principal
investigators) who run trials and want an earlier, explainable signal on retention risk — plus
platform and audit roles who oversee model health, cost, and guardrails.
