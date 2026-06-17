# Guide Layer-3 Network-Isolation Proof — manifests

These manifests author the **layer-3** of the public Guide's three-layer isolation proof
(`/specs/isolation.md` §3): a real-cluster demonstration that, from inside the Guide pod, every
**deny-list** resource is unreachable at the network level (NetworkPolicy denial), while the one
**allowed** egress succeeds (the positive control). Layers 1 (static: import-graph / config-audit /
tool-surface) and 2 (behavioral: red-team / egress-zero) already gate every PR in CI (Gate 7.4);
this is the network-level completion.

**Gate 8.L3-a authors these manifests (static).** Gate 8.L3-b stands up the cluster and runs the
proof. Per `/specs/isolation.md` §3, *any single failure blocks shipping the Guide.*

## ⚠️ Enforcement prerequisite — Calico (or any NetworkPolicy-enforcing CNI)

**Do NOT run this on a default `kind` or `minikube` cluster.** `kind`'s default `kindnet` and
minikube's default CNI **do NOT enforce NetworkPolicies** — the policy objects apply but nothing
acts on them, so the proof would show a **FALSE PASS** (or pass for the wrong reason). 8.L3-b must
create the cluster with **Calico** (e.g. `kind create cluster` with the default CNI disabled +
`kubectl apply -f` Calico, or `minikube start --cni=calico`).

To guard against a non-enforcing cluster, 8.L3-b runs a **negative pre-check**: it confirms a
deny-list target (e.g. `postgres:5432`) IS reachable from the Guide pod **before** the policies are
applied — so the subsequent denial is provably the policy, not a broken network or a missing
service.

## What's in here (kustomize base)

| File | Contents |
|---|---|
| `namespace.yaml` | the `vigil-isolation-proof` namespace |
| `guide.yaml` | the Guide Deployment (`vigil-guide:local`, :8080) + Service — runnable + exec-able |
| `deny-targets.yaml` | the deny-list targets on their REAL ports: `postgres` (:5432), `redis` (:6379), `vault` (:8200, dev mode), `app-api` stand-in (:8000), `model-endpoint` stand-in (:9000) — each a Service |
| `positive-control.yaml` | `allowed-egress` stand-in Service (:8088) — the ONE egress the policy permits |
| `networkpolicies.yaml` | `guide-default-deny-egress` (egress: []) + `guide-egress-allow` (DNS + allowed-egress ONLY) |
| `kustomization.yaml` | ties it together under the namespace |

The deny-list targets hold **no real data** — they are throwaway services that exist only so the
denial is a real denial. The Guide image is built from `guide/Dockerfile` and loaded into the
cluster by 8.L3-b (`kind load docker-image vigil-guide:local`).

## The proof (run by 8.L3-b)

1. `kustomize build` / `kubectl apply -k .` onto the Calico cluster.
2. **Negative pre-check** (before policies, or against an unselected pod): `postgres:5432` etc. ARE
   reachable from the Guide pod → confirms the network + targets work.
3. Apply the NetworkPolicies; from inside the Guide pod (`kubectl exec guide -- python -c "…socket…"`)
   attempt a raw TCP connect to each deny-list `host:port`:
   `postgres:5432`, `redis:6379`, `vault:8200`, `app-api:8000`, `model-endpoint:9000` → **every one
   DENIED** (timeout/refused).
4. **Positive control**: from the same pod, connect to `allowed-egress:8088` → **SUCCEEDS**. This
   proves the policy is *selectively* enforcing, not that the net is merely broken.

## Positive-control reconcile (why not "the vector store")

`/specs/isolation.md` §3 originally named "the approved-document vector store" as the positive
control. Per the 7.0 ratified decision the Guide's store is a **file-backed in-pod index** reached
with **no network call**, so it cannot be a *network* positive control. The gated positive control
is therefore the in-cluster **`allowed-egress`** stand-in the policy explicitly permits. (A live
demo MAY additionally show a real allow-listed LLM-host `:443` connect succeeding; the gated
control is the stand-in, so the proof stays hermetic and internet-independent.)

## Honest scope

This is a **local-cluster demonstration of the NetworkPolicy posture** (kind/minikube, no paid
cluster — `/specs/infra.md`). It proves the policies are correct and enforced on a real cluster;
the **same manifests apply to a production cluster**. It is not itself a production deployment.
Deferred Phase-8 future work (not needed for this proof or the defense): HPA, the full production
k8s for the REAL app (api/worker/scheduler/pgbouncer/Ingress/CronJobs), cloud-KMS Vault
auto-unseal, and pgvector-parity for the Guide store.
