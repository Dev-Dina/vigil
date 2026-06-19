<#
.SYNOPSIS
  One-command live bring-up for the Vigil demo (Gate DEMO-RUNBOOK).

.DESCRIPTION
  1. Loads the demo secrets from .env.demo (gitignored) into the session ($env:) -- never hardcoded.
  2. Ensures the PERSISTENT Vault is running and UNSEALED (api/worker gate on an unsealed Vault).
     If sealed, it prints the unseal instructions and stops (the unseal keys are NEVER in this script).
  3. Runs the verified 3-file / 4-profile live bring-up (app + worker + guide + frontend + mailpit +
     live Langfuse).
  4. Prints the URLs to open and a reminder of the PREP steps (re-seed / score / trigger drift) that
     come next -- see docs/DEMO_RUNBOOK.md PART 1.

  Run from the repo root:  .\demo-up.ps1
  Secrets live in .env.demo (copy .env.demo.example -> .env.demo first). NOTHING here is committed.
#>

$ErrorActionPreference = "Stop"
Set-Location -Path $PSScriptRoot

# --- 1. Load .env.demo (gitignored) -> $env: -----------------------------------------------------
$envFile = Join-Path $PSScriptRoot ".env.demo"
if (-not (Test-Path $envFile)) {
    Write-Host "ERROR: .env.demo not found." -ForegroundColor Red
    Write-Host "  Copy the template and fill in the values:  cp .env.demo.example .env.demo" -ForegroundColor Yellow
    exit 1
}
Get-Content $envFile | ForEach-Object {
    $line = $_.Trim()
    if ($line -and -not $line.StartsWith("#") -and $line.Contains("=")) {
        $idx = $line.IndexOf("=")
        $key = $line.Substring(0, $idx).Trim()
        $val = $line.Substring($idx + 1).Trim().Trim('"').Trim("'")
        Set-Item -Path "env:$key" -Value $val
    }
}

# Fail LOUD if a required secret is missing (never proceed with a half-set session).
$required = @("VAULT_TOKEN", "VIGIL_GUIDE_LLM_API_KEY")
foreach ($name in $required) {
    $item = Get-Item -Path "env:$name" -ErrorAction SilentlyContinue
    if (-not $item -or [string]::IsNullOrWhiteSpace($item.Value)) {
        Write-Host "ERROR: required env var '$name' is empty (set it in .env.demo)." -ForegroundColor Red
        exit 1
    }
}
if ([string]::IsNullOrWhiteSpace($env:VIGIL_LANGFUSE_HOST)) {
    $env:VIGIL_LANGFUSE_HOST = "https://cloud.langfuse.com"
}
Write-Host "Loaded demo secrets from .env.demo (values not echoed)." -ForegroundColor Green

# --- 2. Ensure the persistent Vault is up + UNSEALED --------------------------------------------
# The live override gates api/worker on a HEALTHY (= unsealed) Vault, so an unsealed Vault is a
# precondition for the stack to start. The Vault container is vigil-vault-1.
$vaultRunning = docker ps --filter "name=vigil-vault-1" --filter "status=running" --format "{{.Names}}"
if (-not $vaultRunning) {
    Write-Host "Persistent Vault not running -- starting it..." -ForegroundColor Cyan
    docker compose -f docker-compose.dev.yml -f docker-compose.live.yml --profile app up -d vault
    Start-Sleep -Seconds 3
}

$vaultStatus = (docker exec vigil-vault-1 vault status 2>&1 | Out-String)
if ($vaultStatus -match 'Sealed\s+true') {
    Write-Host ""
    Write-Host "Vault is SEALED. Unseal with 3 of 5 keys (keys live in your password manager," -ForegroundColor Yellow
    Write-Host "NEVER in the repo), then re-run .\demo-up.ps1:" -ForegroundColor Yellow
    Write-Host "  docker exec vigil-vault-1 vault operator unseal <key-1>"
    Write-Host "  docker exec vigil-vault-1 vault operator unseal <key-2>"
    Write-Host "  docker exec vigil-vault-1 vault operator unseal <key-3>"
    Write-Host "Check status with:  docker exec vigil-vault-1 vault status   # Sealed   false" -ForegroundColor DarkGray
    exit 1
}
Write-Host "Vault is unsealed." -ForegroundColor Green

# --- 3. The live bring-up (verified 3-file / 4-profile command) ---------------------------------
# app    = api + worker (+ the persistent vault via the live override)
# guide  = the isolated public Guide on guide-net (live Anthropic via VIGIL_GUIDE_LLM_API_KEY)
# frontend = the Next.js UI (:3000)
# mail   = Mailpit catch-all SMTP (:1025) + inbox (:8025); the mail override flips email->real SMTP
Write-Host "Bringing up the live stack (app + worker + guide + frontend + mailpit + live Langfuse)..." -ForegroundColor Cyan
docker compose `
    -f docker-compose.dev.yml `
    -f docker-compose.live.yml `
    -f docker-compose.mail.yml `
    --profile app --profile guide --profile frontend --profile mail `
    up -d --build

if ($LASTEXITCODE -ne 0) {
    Write-Host "ERROR: docker compose up failed (exit $LASTEXITCODE). Check the output above." -ForegroundColor Red
    exit $LASTEXITCODE
}

# --- 4. URLs + next steps -----------------------------------------------------------------------
Write-Host ""
Write-Host "Stack up. Open:" -ForegroundColor Green
Write-Host "  App UI        http://localhost:3000"
Write-Host "  Mailpit inbox http://localhost:8025"
Write-Host "  Guide (public) http://localhost:8080"
Write-Host "  Langfuse      $env:VIGIL_LANGFUSE_HOST  (your project dashboard)"
Write-Host ""
Write-Host "NEXT (PREP -- see docs/DEMO_RUNBOOK.md PART 1): re-seed -> score each trial 5x as the" -ForegroundColor Yellow
Write-Host "COORDINATORS (coord.a/coord.b, NOT admin) with model_version=sequence_v1.1:demo -> verify" -ForegroundColor Yellow
Write-Host ">=10 champion scores -> trigger the drift breach (mlops@) -> confirm the email in Mailpit ->" -ForegroundColor Yellow
Write-Host "pre-run one assistant turn. Then run the timed DEMO SCRIPT (PART 2)." -ForegroundColor Yellow
