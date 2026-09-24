param(
    [string]$V05Path = "$env:USERPROFILE\Documents\lp_manager_v0_5_live"
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "LP Manager V0.6 - import local V0.5 runtime state" -ForegroundColor Cyan
Write-Host "Source: $V05Path"
Write-Host "Target: $PSScriptRoot"

if (-not (Test-Path $V05Path)) {
    Write-Host "V0.5 folder not found. Pass it explicitly, for example:" -ForegroundColor Yellow
    Write-Host '.\upgrade_from_v05.ps1 -V05Path "C:\Users\deano\Documents\lp_manager_v0_5_live"'
    exit 1
}

$oldEnv = Join-Path $V05Path ".env"
$newEnv = Join-Path $PSScriptRoot ".env"
if ((Test-Path $oldEnv) -and -not (Test-Path $newEnv)) {
    Copy-Item $oldEnv $newEnv
    Write-Host "Copied .env (remains local / gitignored)." -ForegroundColor Green
} elseif (Test-Path $newEnv) {
    Write-Host "Target .env already exists; left untouched." -ForegroundColor Yellow
} else {
    Write-Host "No V0.5 .env found. Run configure_live.ps1 after setup." -ForegroundColor Yellow
}

$oldDb = Join-Path $V05Path "data\lp_manager.sqlite3"
$newData = Join-Path $PSScriptRoot "data"
$newDb = Join-Path $newData "lp_manager.sqlite3"
if ((Test-Path $oldDb) -and -not (Test-Path $newDb)) {
    New-Item -ItemType Directory -Force -Path $newData | Out-Null
    Copy-Item $oldDb $newDb
    Write-Host "Copied V0.5 database. V0.6 will migrate it on first start." -ForegroundColor Green
} elseif (Test-Path $newDb) {
    Write-Host "Target database already exists; left untouched." -ForegroundColor Yellow
} else {
    Write-Host "No V0.5 database found; V0.6 will create a fresh one." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Runtime state import complete. Next:" -ForegroundColor Cyan
Write-Host "  python -m venv .venv"
Write-Host "  Set-ExecutionPolicy -Scope Process Bypass"
Write-Host "  .\.venv\Scripts\Activate.ps1"
Write-Host "  python -m pip install -r requirements.txt"
Write-Host "  python .\start_lp_manager.py"
