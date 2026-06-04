# Infra Spec

## Decisions (fixed)
- Modular monolith (FastAPI) + worker + scheduler.
- Vault (secrets, JWT signing key, dynamic DB creds). Postgres + pgvector + RLS.
- Redis (revocable sessions, cache, rate limiting). Arq queue (Redis-backed).
- Two surfaces: local app + isolated public Guide.
- Scaling: cheap path scales horizontally (async + PgBouncer pool + Redis cache);
  expensive path absorbed by the queue (bounded workers, backpressure, jittered retries).

## Topology
TODO: services, ports, which components share which datastores, the isolation boundary.

## Production-readiness (Phase 8)
TODO: Docker Compose (dev) + deploy/k8s/ (Deployments, Services, Ingress, ConfigMaps, Secrets,
Jobs/CronJobs, NetworkPolicies, optional HPA), runnable on kind/minikube. No paid cluster.
