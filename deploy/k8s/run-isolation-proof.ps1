<#
.SYNOPSIS
  Gate 8.L3-b - the Guide layer-3 NetworkPolicy isolation proof (Windows PowerShell runner).

.DESCRIPTION
  Stands up a kind cluster with Calico (a NetworkPolicy-ENFORCING CNI), applies the 8.L3-a
  manifests, and proves the Guide pod cannot reach any deny-list resource at the network level -
  with a NEGATIVE PRE-CHECK that keeps the proof honest (targets must be reachable BEFORE the
  policy, so the later denial is provably the policy and not a broken network / wrong CNI).

  Sequence: create cluster -> install Calico -> build+load the guide image -> apply targets
  (NOT the policies) -> NEGATIVE PRE-CHECK (deny-list targets REACHABLE) -> apply the guide
  NetworkPolicies -> DENIAL TEST (deny-list targets DENIED) -> POSITIVE CONTROL (allowed-egress
  OK) -> PASS/FAIL summary + transcript -> teardown (unless -Keep).

  This is a LOCAL kind demonstration of the NetworkPolicy posture (no paid cluster). The SAME
  manifests apply to a production cluster. It proves the policy is correct AND enforced.

.PARAMETER Keep
  Leave the cluster running afterwards (for the live side-by-side demo). Default: tear down.

.EXAMPLE
  pwsh ./deploy/k8s/run-isolation-proof.ps1
  pwsh ./deploy/k8s/run-isolation-proof.ps1 -Keep
#>
[CmdletBinding()]
param(
  [switch]$Keep
)

$ErrorActionPreference = "Stop"

# --- pinned config -----------------------------------------------------------------------------
$Cluster   = "vigil-guide-proof"
$Ns        = "vigil-isolation-proof"
$Image     = "vigil-guide:local"
$CalicoVer = "v3.27.3"
$CalicoUrl = "https://raw.githubusercontent.com/projectcalico/calico/$CalicoVer/manifests/calico.yaml"

# Repo root = two levels up from deploy/k8s/.
$ScriptDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$RepoRoot  = (Resolve-Path (Join-Path $ScriptDir "..\..")).Path
$K8sDir    = $ScriptDir
$Transcript = Join-Path $K8sDir "last-proof-transcript.txt"

# Deny-list targets (service:port) - must be DENIED after the policy. Positive control allowed.
$DenyTargets = @(
  @{ name = "postgres";       port = 5432 },
  @{ name = "redis";          port = 6379 },
  @{ name = "vault";          port = 8200 },
  @{ name = "app-api";        port = 8000 },
  @{ name = "model-endpoint"; port = 9000 }
)
$PositiveTarget = @{ name = "allowed-egress"; port = 8088 }

# Single-line python probe: connect (3s timeout) and close; raises -> non-zero exit on failure.
$Probe = 'import socket,sys; socket.create_connection((sys.argv[1],int(sys.argv[2])),timeout=3).close()'

$Results = [System.Collections.Generic.List[object]]::new()
function Add-Result($target, $pre, $post) {
  $Results.Add([pscustomobject]@{ Target = $target; PreCheck = $pre; PostPolicy = $post })
}

function Require-Tool($name) {
  if (-not (Get-Command $name -ErrorAction SilentlyContinue)) {
    throw "Required tool '$name' not found on PATH. Prerequisites: Docker, kind, kubectl."
  }
}

# Connect from INSIDE the guide pod. Returns $true if reachable, $false if denied/refused.
function Test-PodConnect($targetHost, $port) {
  kubectl exec -n $Ns deploy/guide -- python -c $Probe $targetHost "$port" *> $null
  return ($LASTEXITCODE -eq 0)
}

Start-Transcript -Path $Transcript -Force | Out-Null
try {
  Write-Host "== Guide layer-3 isolation proof ==" -ForegroundColor Cyan
  Require-Tool docker; Require-Tool kind; Require-Tool kubectl

  Write-Host "`n[1/8] Creating kind cluster '$Cluster' (default CNI DISABLED for Calico)..."
  kind create cluster --name $Cluster --config (Join-Path $K8sDir "kind/kind-config.yaml")

  Write-Host "`n[2/8] Installing Calico $CalicoVer (NetworkPolicy-enforcing CNI)..."
  kubectl apply -f $CalicoUrl
  Write-Host "    waiting for Calico + CoreDNS to be ready (up to 5 min)..."
  kubectl wait --for=condition=ready pods -l k8s-app=calico-node -n kube-system --timeout=300s
  kubectl wait --for=condition=ready pods -l k8s-app=kube-dns   -n kube-system --timeout=300s

  Write-Host "`n[3/8] Building + loading the guide image ($Image)..."
  docker build -t $Image -f (Join-Path $RepoRoot "guide/Dockerfile") $RepoRoot
  kind load docker-image $Image --name $Cluster

  Write-Host "`n[4/8] Applying namespace + guide + deny-list targets + positive control (NOT the policies)..."
  kubectl apply -f (Join-Path $K8sDir "namespace.yaml")
  kubectl apply -n $Ns `
    -f (Join-Path $K8sDir "guide.yaml") `
    -f (Join-Path $K8sDir "deny-targets.yaml") `
    -f (Join-Path $K8sDir "positive-control.yaml")
  Write-Host "    waiting for all deployments to be available (up to 5 min)..."
  kubectl wait --for=condition=available deploy --all -n $Ns --timeout=300s

  Write-Host "`n[5/8] NEGATIVE PRE-CHECK (before policy): every deny-list target must be REACHABLE..."
  $preOk = $true
  foreach ($t in $DenyTargets) {
    $r = Test-PodConnect $t.name $t.port
    Write-Host ("    pre  {0,-16}:{1,-5} -> {2}" -f $t.name, $t.port, ($(if ($r) { 'REACHABLE' } else { 'UNREACHABLE' })))
    if (-not $r) { $preOk = $false }
  }
  $posPre = Test-PodConnect $PositiveTarget.name $PositiveTarget.port
  Write-Host ("    pre  {0,-16}:{1,-5} -> {2}" -f $PositiveTarget.name, $PositiveTarget.port, ($(if ($posPre) { 'REACHABLE' } else { 'UNREACHABLE' })))
  if (-not $preOk -or -not $posPre) {
    throw "NEGATIVE PRE-CHECK FAILED: a target was not reachable before the policy. The cluster/CNI/targets are not healthy - the denial test would be meaningless. (Is Calico up? Are pods ready?)"
  }

  Write-Host "`n[6/8] Applying the guide NetworkPolicies (default-deny-egress + guide-egress-allow)..."
  kubectl apply -n $Ns -f (Join-Path $K8sDir "networkpolicies.yaml")
  Write-Host "    pausing 8s for policy propagation..."
  Start-Sleep -Seconds 8

  Write-Host "`n[7/8] DENIAL TEST (after policy): every deny-list target must be DENIED..."
  foreach ($t in $DenyTargets) {
    $r = Test-PodConnect $t.name $t.port
    Write-Host ("    post {0,-16}:{1,-5} -> {2}" -f $t.name, $t.port, ($(if ($r) { 'REACHABLE (LEAK!)' } else { 'DENIED' })))
    Add-Result ("{0}:{1}" -f $t.name, $t.port) "reachable" ($(if ($r) { "REACHABLE" } else { "denied" }))
  }

  Write-Host "`n[8/8] POSITIVE CONTROL (after policy): the one allowed egress must SUCCEED..."
  $posPost = Test-PodConnect $PositiveTarget.name $PositiveTarget.port
  Write-Host ("    post {0,-16}:{1,-5} -> {2}" -f $PositiveTarget.name, $PositiveTarget.port, ($(if ($posPost) { 'OK' } else { 'BLOCKED (policy too strict!)' })))
  Add-Result ("{0}:{1}" -f $PositiveTarget.name, $PositiveTarget.port) "reachable" ($(if ($posPost) { "OK (allowed)" } else { "BLOCKED" }))

  # --- verdict (from the recorded results; a deny target still "REACHABLE" post-policy = leak) ---
  $leaks = $Results | Where-Object { $_.Target -ne ("{0}:{1}" -f $PositiveTarget.name, $PositiveTarget.port) -and $_.PostPolicy -eq "REACHABLE" }
  $pass = ($leaks.Count -eq 0) -and $posPost

  Write-Host "`n================ SUMMARY ================" -ForegroundColor Cyan
  $Results | Format-Table -AutoSize | Out-String | Write-Host
  if ($pass) {
    Write-Host "RESULT: PASS - every deny-list target DENIED after the policy; positive control OK." -ForegroundColor Green
    Write-Host "Layer-3 NetworkPolicy isolation demonstrated on a local Calico-enforced kind cluster."
  } else {
    Write-Host "RESULT: FAIL - a deny-list target was reachable, or the positive control was blocked." -ForegroundColor Red
    Write-Host "Any single failure blocks shipping the Guide (specs/isolation.md section 3)."
  }
  Write-Host "Transcript: $Transcript"
  Write-Host "=========================================" -ForegroundColor Cyan
}
finally {
  Stop-Transcript | Out-Null
  if ($Keep) {
    Write-Host "`n-Keep set: leaving cluster '$Cluster' up. Tear down later with:"
    Write-Host "    kind delete cluster --name $Cluster"
  } else {
    Write-Host "`nTearing down cluster '$Cluster'..."
    kind delete cluster --name $Cluster | Out-Null
  }
}

if (-not $pass) { exit 1 }
