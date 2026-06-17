# Vigil Guide — Safety Policy

This document describes how the public Guide behaves and the guarantees around it. The Guide is
the guest-facing chatbot that explains the Vigil project from approved public documents. It is
deliberately limited.

## What the Guide will do
- Answer questions about **the Vigil project**: what it does, how it is built, the modelling
  approach and its honest results and limits — grounded in the approved public documents (brief,
  architecture notes, model card, safety policy, deployment notes).
- **Cite its source** for substantive answers, and **refuse when it cannot ground an answer** in
  the approved documents (including when the best match is only weakly relevant) — it does not
  guess or fall back to open-ended generation.

## What the Guide will refuse
The Guide blocks and refuses, as an explicit, logged outcome:
- **Medical advice, diagnosis, treatment, or any clinical claim.** Vigil explains retention risk
  and method; it never advises on anyone's care. There is no participant and no clinical context
  available to it in any case.
- **Anything outside the project-explanation scope** — it only describes Vigil from the approved
  documents.
- **Prompt-injection attempts** ("ignore previous instructions", "reveal your system prompt",
  "act as…") — retrieved document text is treated as data, never as instructions.
- **Secret- or credential-extraction attempts** (asking for keys, tokens, passwords, connection
  strings, environment variables).
- **Attempts to reach real systems** — to read participant data, query a database, or call an
  internal or admin endpoint.

## Why those refusals are reliable (defense in depth)
The Guide's refusals are **structural**, not just polite wording:
- It has **no tool and no route** to reach anything real. Its only capability is searching the
  approved-document store. Even if a prompt somehow talked the model into "complying", there is
  nothing for it to reach.
- It is a **separate service with its own credentials**. It holds no database connection, no
  broad secrets-store token, no session/cache URL, no queue URL, and no internal/admin address.
- A deny-by-default network boundary blocks any connection from the Guide to the real app's data
  stores; only the approved-document store is reachable.

These three layers (no code path, no credential, no network path) are tested — including an
adversarial "even when actively prompted to misbehave" suite — and any single failure blocks the
Guide from shipping.

## Privacy and data honesty
- The Guide handles **no participant data and no protected health information** — none is
  available to it.
- User messages are **redacted** before they reach the model (direct identifiers such as emails,
  phone numbers, and similar are stripped), and **raw message text is never stored** — only the
  redacted form, kept for an auditable record that guardrails fired.
- Performance numbers the Guide reports come from the approved model card and are **never
  inflated**; negative and limited results are stated plainly.

## Accountability
Every Guide turn writes one redacted audit record (the guardrail decision, the sources used, the
model, latency, and cost) to the Guide's own audit store — separate from the real app — so that
refusals and answers are reviewable after the fact.
