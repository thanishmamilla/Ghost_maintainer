<#
.SYNOPSIS
    Start Ghost Maintainer for a demo recording.

.DESCRIPTION
    Verifies the prerequisites, makes sure dependencies are installed, starts the
    backend and frontend in separate windows, waits until both answer, and opens
    the dashboard.

    Use -Offline to force the captured metadata fixtures. That makes the scan
    reproducible and immune to a registry outage during recording.

.EXAMPLE
    .\scripts\demo.ps1
    .\scripts\demo.ps1 -Offline
#>

param(
    [switch]$Offline
)

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot
$backend = Join-Path $root "backend"
$frontend = Join-Path $root "frontend"
$demo = Join-Path $root "demo-rotten-app"

function Say($message) { Write-Host "  $message" }

Write-Host ""
Write-Host "Ghost Maintainer - demo launcher"
Write-Host "================================"
Write-Host ""

# ---------------------------------------------------------------------------
# prerequisites
# ---------------------------------------------------------------------------

Write-Host "Checking prerequisites"

$python = Join-Path $backend ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Host "  Creating the backend virtualenv..."
    Push-Location $backend
    uv venv .venv --python 3.11
    uv pip install --python .venv\Scripts\python.exe -r requirements.txt
    Pop-Location
}
Say "backend virtualenv ok"

if (-not (Test-Path (Join-Path $frontend "node_modules"))) {
    Write-Host "  Installing frontend dependencies..."
    Push-Location $frontend
    npm install --no-fund --no-audit
    Pop-Location
}
Say "frontend dependencies ok"

if (-not (Test-Path (Join-Path $demo "node_modules"))) {
    Write-Host "  Installing demo repository dependencies (this takes about a minute)..."
    Push-Location $demo
    npm install --no-fund --no-audit
    Pop-Location
}
Say "demo repository ok (node_modules present, so fixes run in seconds)"

# ---------------------------------------------------------------------------
# configuration
# ---------------------------------------------------------------------------

$envFile = Join-Path $backend ".env"
if (-not (Test-Path $envFile)) {
    Copy-Item (Join-Path $backend ".env.example") $envFile
    Say "created backend\.env from the example"
}

# Without Docker the sandbox needs explicit consent for the local runner.
$envText = Get-Content $envFile -Raw
if ($envText -notmatch "(?m)^ALLOW_UNTRUSTED_LOCAL_EXECUTION=true") {
    $envText = $envText -replace "(?m)^ALLOW_UNTRUSTED_LOCAL_EXECUTION=false", "ALLOW_UNTRUSTED_LOCAL_EXECUTION=true"
    Set-Content -Path $envFile -Value $envText -NoNewline
    Say "enabled the local sandbox runner (send this env file to anyone who records)"
}

# Keep the fixture switch in sync with how this run was launched, so a later
# recording is not left silently offline.
$envText = Get-Content $envFile -Raw
$desired = if ($Offline) { "true" } else { "false" }
if ($envText -match "(?m)^OFFLINE_FIXTURES=") {
    $envText = $envText -replace "(?m)^OFFLINE_FIXTURES=.*$", "OFFLINE_FIXTURES=$desired"
} else {
    $envText = $envText.TrimEnd() + "`nOFFLINE_FIXTURES=$desired`n"
}
Set-Content -Path $envFile -Value $envText -NoNewline

if ($Offline) {
    Say "offline fixtures enabled - the scan will not touch the network"
} else {
    Say "offline fixtures disabled - the scan will use live registry data"
}

# ---------------------------------------------------------------------------
# start
# ---------------------------------------------------------------------------

Write-Host ""
Write-Host "Starting servers"

Start-Process -FilePath "powershell" -ArgumentList @(
    "-NoExit", "-Command",
    "Set-Location '$backend'; .venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000"
)
Say "backend starting on http://localhost:8000"

Start-Process -FilePath "powershell" -ArgumentList @(
    "-NoExit", "-Command",
    "Set-Location '$frontend'; npm run dev"
)
Say "frontend starting on http://localhost:5173"

Write-Host ""
Write-Host "Waiting for both to answer..."

$apiReady = $false
for ($attempt = 0; $attempt -lt 40; $attempt++) {
    try {
        $health = Invoke-RestMethod "http://127.0.0.1:8000/health" -TimeoutSec 2
        if ($health.status -eq "ok") { $apiReady = $true; break }
    } catch { }
    Start-Sleep -Milliseconds 500
}

$uiReady = $false
for ($attempt = 0; $attempt -lt 40; $attempt++) {
    try {
        $response = Invoke-WebRequest "http://localhost:5173" -UseBasicParsing -TimeoutSec 2
        if ($response.StatusCode -eq 200) { $uiReady = $true; break }
    } catch { }
    Start-Sleep -Milliseconds 500
}

Write-Host ""
if ($apiReady -and $uiReady) {
    Write-Host "Ready." -ForegroundColor Green
    Write-Host ""
    Write-Host "  Dashboard : http://localhost:5173  (use localhost, not 127.0.0.1)"
    Write-Host "  API docs  : http://localhost:8000/docs"
    Write-Host "  Sandbox   : $($health.sandbox_mode)  |  dry run: $($health.dry_run)  |  LLM: $(if ($health.llm_configured) { 'on' } else { 'off' })"
    Write-Host ""
    Write-Host "  1. Press 'Demo repo'"
    Write-Host "  2. Select jsonwebtoken (rot 90, not observed)"
    Write-Host "  3. Select lodash (rot 48, observed) and press 'Adopt fix'"
    Write-Host "  4. Then select moment and adopt the package replacement"
    Write-Host ""
    Start-Process "http://localhost:5173"
} else {
    Write-Host "Something did not start." -ForegroundColor Yellow
    Write-Host "  API responding : $apiReady"
    Write-Host "  UI responding  : $uiReady"
    Write-Host "  Check the two PowerShell windows for errors."
    exit 1
}