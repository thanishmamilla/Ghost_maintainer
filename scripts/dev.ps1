<#
.SYNOPSIS
    Start Ghost Maintainer (backend + frontend) in two windows.

.DESCRIPTION
    Creates the backend virtualenv and the frontend node_modules on first run,
    then launches uvicorn on :8000 and Vite on :5173.

.EXAMPLE
    .\scripts\dev.ps1
#>

$ErrorActionPreference = "Stop"
$root = Split-Path -Parent $PSScriptRoot

if (-not (Test-Path (Join-Path $root "backend\.venv"))) {
    Write-Host "Creating the backend virtualenv..."
    Push-Location (Join-Path $root "backend")
    uv venv .venv --python 3.11
    uv pip install --python .venv\Scripts\python.exe -r requirements.txt
    Pop-Location
}

if (-not (Test-Path (Join-Path $root "frontend\node_modules"))) {
    Write-Host "Installing frontend dependencies..."
    Push-Location (Join-Path $root "frontend")
    npm install --no-fund --no-audit
    Pop-Location
}

if (-not (Test-Path (Join-Path $root "demo-rotten-app\node_modules"))) {
    Write-Host "Installing demo repository dependencies..."
    Push-Location (Join-Path $root "demo-rotten-app")
    npm install --no-fund --no-audit
    Pop-Location
}

Write-Host "Starting the backend on http://localhost:8000 ..."
Start-Process -FilePath "powershell" -ArgumentList @(
    "-NoExit", "-Command",
    "Set-Location '$root\backend'; .venv\Scripts\python.exe -m uvicorn app.main:app --reload --port 8000"
)

Write-Host "Starting the frontend on http://localhost:5173 ..."
Start-Process -FilePath "powershell" -ArgumentList @(
    "-NoExit", "-Command",
    "Set-Location '$root\frontend'; npm run dev"
)

Write-Host ""
Write-Host "Ghost Maintainer is starting."
Write-Host "  Dashboard: http://localhost:5173"
Write-Host "  API docs:  http://localhost:8000/docs"