# Vigil — Deployment Notes (public overview)

A high-level, public description of how Vigil is deployed. It intentionally omits internal hosts,
addresses, and credentials — those are never public.

## Two surfaces, one hard boundary
Vigil runs as two deployment surfaces with a deliberately empty intersection:

- **The app side (trusted):** the API, background workers, and scheduler, sharing the application
  datastore plane (database, cache/session store, secrets manager). All data access goes through
  the tenant-scoped, row-level-security path.
- **The Guide side (this service, untrusted):** a standalone chatbot service with its **own**
  deployment and **own** credentials, whose only data source is a read-only store of approved
  public documents.

The boundary between them is the load-bearing line of the whole design: the Guide shares **no
datastore, no credential, and no network path** with the app side.

## How the boundary is enforced (three independent layers)
1. **Separate service / no shared code:** the Guide is its own application and imports nothing
   from the real app. Its capability is exactly one thing — searching the approved-document store.
2. **Separate credentials:** the Guide's entire secret set is its own document store, its own
   audit (message-events) sink, and its own model-provider key. It holds no database credential,
   no broad secrets-store token, no cache/queue URL, and no internal/admin address.
3. **Network policy:** in the containerized/orchestrated deployment, a deny-by-default policy
   blocks the Guide from reaching the app's database, cache, secrets manager, model endpoints, or
   API; only the approved-document store is reachable. A positive control confirms the policy is
   selectively enforcing (the approved store works) rather than the network merely being broken.

## Running it
The whole system runs **locally** — on Docker Compose for development and on a local Kubernetes
cluster (kind / minikube) for the deployment demo — with **no paid hosting required**. In the
local stack the Guide sits on a separate network from the app's datastores, so the isolation
boundary holds even in development. The deployment demo can show, side by side, a blocked
connection from the Guide to a real datastore failing while an approved-document query succeeds.

## Status and honesty
This is a **portfolio / demonstration** deployment. It uses real public registry data in
aggregate and a clearly-labelled synthetic cohort for the trajectory modelling; it processes no
real participant data and no protected health information. The production-hardening items
(managed key auto-unseal, the full network-policy integration job, horizontal autoscaling) are
tracked as later-phase work and are described honestly as such rather than implied to be in place.
