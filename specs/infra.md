# Infra Spec

## Decisions (fixed)
- Modular monolith (FastAPI) + worker + scheduler.
- Vault (secrets, JWT signing key, dynamic DB creds). Postgres + pgvector + RLS.
- Redis (revocable sessions, cache, rate limiting). Arq queue (Redis-backed).
- Two surfaces: local app + isolated public Guide.
- Scaling: cheap path scales horizontally (async + PgBouncer pool + Redis cache);
  expensive path absorbed by the queue (bounded workers, backpressure, jittered retries).

## Topology
Two surfaces, one shared datastore plane for the real app and a **deliberately empty intersection**
for the Guide. The isolation boundary (`/specs/isolation.md`) is the load-bearing line below: the
Guide shares no datastore, no credential, and no network path with anything on the app side.

### Services and ports
| Service | Port | Surface | Role |
|---|---|---|---|
| `api` (FastAPI modular monolith) | `8000` | local app | HTTP API under `/api/v1`; auth/scope dependency; enqueues slow work |
| `worker` (Arq) | — (no ingress) | local app | consumes the queue: LLM/agent, reports, ingestion, drift; bounded concurrency + backpressure |
| `scheduler` (Arq cron) | — (no ingress) | local app | jittered scheduled jobs (drift checks, rollups) |
| `guide` (FastAPI, isolated) | `8080` | public Guide | guest chatbot; OWN deployment + OWN credentials; reads ONLY the approved-doc vector store |
| `postgres` (+ pgvector, RLS) | `5432` | app only | tenant data, `ref_*` reference, model/monitoring tables, vector collections |
| `pgbouncer` | `6432` | app only | connection pool in front of Postgres (cheap-path horizontal scale) |
| `redis` | `6379` | app only | revocable sessions, cache, rate-limit, Arq queue backend |
| `vault` | `8200` | app only | secrets, JWT signing key, dynamic DB creds |
| model endpoints (LLM/inference) | provider/`:<svc>` | app only | reached by `worker`/`api` only, never by `guide` |
| approved-doc vector store | `:<vec>` | **shared boundary** | the Guide's ONLY data source; read-only for `guide` |

### Who shares which datastore
- **app side** (`api`, `worker`, `scheduler`): share `postgres` (via `pgbouncer`), `redis`, and
  `vault`. Postgres access is always through the tenant-scoped session (RLS); credentials are
  dynamic, issued by Vault. These three services are the only things that hold a participant-DB
  DSN, a broad Vault token, the Redis URL, or the queue URL.
- **Guide side** (`guide`): shares **nothing** with the app side. Its only outbound dependencies
  are (a) the read-only approved-doc vector store, (b) its own `message_events` sink (guardrail
  proof), (c) the LLM provider key. It has no Postgres DSN, no Vault token, no Redis URL, no
  internal/admin API base URL — asserted by the config/secret audit (`/specs/isolation.md` §1).
- **`message_events`**: the app writes the real-app assistant's rows to the app sink; the Guide
  writes to its OWN sink. Same schema (`/specs/observability.md`), separate stores — the Guide
  never writes into the app's Postgres.

### Isolation boundary (the line that must never be crossed)
```
        ┌──────────────── app side (trusted) ─────────────────┐
guest → │  api:8000 ─┬─ worker ─┬─ scheduler                  │
        │            │          │                             │
        │   pgbouncer:6432 → postgres:5432 (RLS)              │
        │   redis:6379    vault:8200    model endpoints       │
        └────────────────────────────────────────────────────┘
                              ╳  no shared datastore / cred / route  ╳
        ┌──────────────── Guide side (untrusted) ─────────────┐
guest → │  guide:8080 ──→ approved-doc vector store (RO)       │
        │              └─→ guide message_events sink           │
        └─────────────────────────────────────────────────────┘
```
NetworkPolicies (below) enforce the `╳`: from the `guide` pod, Postgres/Redis/Vault/model
endpoints/the app Service are DENIED; the approved-doc vector store is the only ALLOWED egress.

## Production-readiness (Phase 8)
Everything runs locally on **kind/minikube** — no paid cluster. Two deployment surfaces:

### Docker Compose (dev)
`docker-compose.yml` brings up the full app for local development: `api`, `worker`, `scheduler`,
`guide`, `postgres` (pgvector image, RLS migrations applied), `pgbouncer`, `redis`, `vault`
(dev mode), and the approved-doc vector store. Secrets come from Vault dev, not the compose file.
`guide` is on a **separate compose network** from the app datastores so even in dev it cannot
reach Postgres/Redis/Vault — the boundary holds locally too. One `make up` / `docker compose up`
gives a runnable two-surface stack.

### `deploy/k8s/` (kind/minikube)
Plain manifests (kustomize base + overlays), applied to a local kind cluster:
- **Deployments**: `api`, `worker`, `scheduler`, `guide`; StatefulSets/Deployments for `postgres`,
  `redis`, `vault`, `pgbouncer`, vector store.
- **Services**: ClusterIP per component; Ingress for `api` (`/api/v1`) and `guide` on distinct
  hosts/paths. `worker`/`scheduler` have no Service (no ingress).
- **ConfigMaps**: non-secret config (routing tables, model registry pointers, log level).
- **Secrets**: sourced from Vault; the `guide`'s secret set contains ONLY the vector-store
  endpoint, its `message_events` sink, and the LLM key — never an app DSN/token.
- **Jobs / CronJobs**: build-time AACT ingestion as a Job; drift/rollup as jittered CronJobs.
- **NetworkPolicies** (the demo): default-deny in the namespace; explicit allows for app-side
  pods → `postgres`/`redis`/`vault`/model endpoints; a `guide` policy that DENIES all of those and
  ALLOWS only the approved-doc vector store. This is exactly what the layer-3 isolation integration
  job asserts (`/specs/isolation.md` §3): from the `guide` pod, deny-list resources are unreachable
  while the vector store succeeds.
- **Optional HPA**: on `api` and `worker` to show the cheap path scaling horizontally.
The whole stack stands up on kind via one script; the isolation integration job runs against it.
