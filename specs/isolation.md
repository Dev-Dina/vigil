# Isolation Spec — Public Guide allow/deny

The most important contract in the repo. The public Guide must be provably unable to reach
anything real.

## Decisions (fixed)
- The Guide is a SEPARATE service with its OWN deployment and OWN credentials.
- It may read ONLY the approved-document vector store.
- Enforced in three independent layers: separate service, separate credentials, NetworkPolicies.

## MAY touch (allow-list)
- The approved-document vector store (read-only): brief, architecture notes, model card,
  safety policy, deployment notes.
- Its own `message_events` sink (for guardrail proof).

## MUST NOT touch (deny-list)
- The participant database, the dashboard/API, model endpoints, admin APIs, internal tools,
  Vault, Redis, the real queue, or any real credential.

## Guardrails the Guide enforces
- Block: medical advice, diagnosis, clinical claims, prompt injection, secret extraction,
  anything outside portfolio/app-explanation scope.

## Proof obligation
TODO: define the isolation test — assert the Guide cannot reach any deny-list resource
EVEN WHEN PROMPTED TO. NetworkPolicy denial is part of the demo.
