#!/usr/bin/env bash
# Gate 8.L3-b — the Guide layer-3 NetworkPolicy isolation proof (portable bash runner).
#
# Stands up a kind cluster with Calico (a NetworkPolicy-ENFORCING CNI), applies the 8.L3-a
# manifests, and proves the Guide pod cannot reach any deny-list resource at the network level —
# with a NEGATIVE PRE-CHECK (targets reachable BEFORE the policy) so the later denial is provably
# the policy, not a broken network / wrong CNI. Local kind demonstration of the policy posture;
# the same manifests apply to production.
#
# Usage:  ./deploy/k8s/run-isolation-proof.sh            # run + tear down
#         ./deploy/k8s/run-isolation-proof.sh --keep     # leave cluster up for the live demo
# Prereqs on PATH: docker, kind, kubectl.
set -euo pipefail

CLUSTER="vigil-guide-proof"
NS="vigil-isolation-proof"
IMAGE="vigil-guide:local"
CALICO_VER="v3.27.3"
CALICO_URL="https://raw.githubusercontent.com/projectcalico/calico/${CALICO_VER}/manifests/calico.yaml"

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(cd "${SCRIPT_DIR}/../.." && pwd)"
TRANSCRIPT="${SCRIPT_DIR}/last-proof-transcript.txt"

KEEP=0
[[ "${1:-}" == "--keep" ]] && KEEP=1

# service:port — deny-list targets (must be DENIED post-policy); positive control is allowed.
DENY_TARGETS=("postgres:5432" "redis:6379" "vault:8200" "app-api:8000" "model-endpoint:9000")
POSITIVE="allowed-egress:8088"
PROBE='import socket,sys; socket.create_connection((sys.argv[1],int(sys.argv[2])),timeout=3).close()'

# Connect from INSIDE the guide pod: exit 0 = reachable, non-zero = denied/refused.
pod_connect() { # host port
  kubectl exec -n "$NS" deploy/guide -- python -c "$PROBE" "$1" "$2" >/dev/null 2>&1
}

require() { command -v "$1" >/dev/null 2>&1 || { echo "Required tool '$1' not on PATH (need: docker, kind, kubectl)"; exit 2; }; }

teardown() {
  if [[ "$KEEP" == "1" ]]; then
    echo; echo "--keep set: leaving cluster '$CLUSTER' up. Tear down later with: kind delete cluster --name $CLUSTER"
  else
    echo; echo "Tearing down cluster '$CLUSTER'..."; kind delete cluster --name "$CLUSTER" >/dev/null 2>&1 || true
  fi
}
trap teardown EXIT

exec > >(tee "$TRANSCRIPT") 2>&1

echo "== Guide layer-3 isolation proof =="
require docker; require kind; require kubectl

echo; echo "[1/8] Creating kind cluster '$CLUSTER' (default CNI DISABLED for Calico)..."
kind create cluster --name "$CLUSTER" --config "${SCRIPT_DIR}/kind/kind-config.yaml"

echo; echo "[2/8] Installing Calico ${CALICO_VER} (NetworkPolicy-enforcing CNI)..."
kubectl apply -f "$CALICO_URL"
echo "    waiting for Calico + CoreDNS ready (up to 5 min)..."
kubectl wait --for=condition=ready pods -l k8s-app=calico-node -n kube-system --timeout=300s
kubectl wait --for=condition=ready pods -l k8s-app=kube-dns -n kube-system --timeout=300s

echo; echo "[3/8] Building + loading the guide image (${IMAGE})..."
docker build -t "$IMAGE" -f "${REPO_ROOT}/guide/Dockerfile" "$REPO_ROOT"
kind load docker-image "$IMAGE" --name "$CLUSTER"

echo; echo "[4/8] Applying namespace + guide + deny-list targets + positive control (NOT the policies)..."
kubectl apply -f "${SCRIPT_DIR}/namespace.yaml"
kubectl apply -n "$NS" \
  -f "${SCRIPT_DIR}/guide.yaml" \
  -f "${SCRIPT_DIR}/deny-targets.yaml" \
  -f "${SCRIPT_DIR}/positive-control.yaml"
echo "    waiting for all deployments available (up to 5 min)..."
kubectl wait --for=condition=available deploy --all -n "$NS" --timeout=300s

echo; echo "[5/8] NEGATIVE PRE-CHECK (before policy): every deny-list target must be REACHABLE..."
pre_ok=1
for t in "${DENY_TARGETS[@]}" "$POSITIVE"; do
  host="${t%%:*}"; port="${t##*:}"
  if pod_connect "$host" "$port"; then echo "    pre  ${t} -> REACHABLE"; else echo "    pre  ${t} -> UNREACHABLE"; pre_ok=0; fi
done
if [[ "$pre_ok" != "1" ]]; then
  echo "NEGATIVE PRE-CHECK FAILED: a target was unreachable before the policy — denial test would be meaningless (is Calico up? pods ready?)."; exit 1
fi

echo; echo "[6/8] Applying the guide NetworkPolicies (default-deny-egress + guide-egress-allow)..."
kubectl apply -n "$NS" -f "${SCRIPT_DIR}/networkpolicies.yaml"
echo "    pausing 8s for policy propagation..."; sleep 8

echo; echo "[7/8] DENIAL TEST (after policy): every deny-list target must be DENIED..."
leak=0
for t in "${DENY_TARGETS[@]}"; do
  host="${t%%:*}"; port="${t##*:}"
  if pod_connect "$host" "$port"; then echo "    post ${t} -> REACHABLE (LEAK!)"; leak=1; else echo "    post ${t} -> DENIED"; fi
done

echo; echo "[8/8] POSITIVE CONTROL (after policy): the one allowed egress must SUCCEED..."
pos_ok=0
host="${POSITIVE%%:*}"; port="${POSITIVE##*:}"
if pod_connect "$host" "$port"; then echo "    post ${POSITIVE} -> OK (allowed)"; pos_ok=1; else echo "    post ${POSITIVE} -> BLOCKED (policy too strict!)"; fi

echo; echo "================ SUMMARY ================"
if [[ "$leak" == "0" && "$pos_ok" == "1" ]]; then
  echo "RESULT: PASS — every deny-list target DENIED after the policy; positive control OK."
  echo "Layer-3 NetworkPolicy isolation demonstrated on a local Calico-enforced kind cluster."
  echo "Transcript: $TRANSCRIPT"; echo "========================================="
else
  echo "RESULT: FAIL — a deny-list target was reachable, or the positive control was blocked."
  echo "Any single failure blocks shipping the Guide (specs/isolation.md §3)."
  echo "Transcript: $TRANSCRIPT"; echo "========================================="
  exit 1
fi
