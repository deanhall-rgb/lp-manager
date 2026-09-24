param(
    [string]$V06Path = "C:\Users\deano\Documents\lp_manager_v0_6"
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "LP Manager V0.7 upgrade helper" -ForegroundColor Cyan
Write-Host "Source V0.6: $V06Path"

if (-not (Test-Path $V06Path)) {
    throw "V0.6 folder not found: $V06Path"
}

$sourceEnv = Join-Path $V06Path ".env"
$targetEnv = Join-Path $PSScriptRoot ".env"
if ((Test-Path $sourceEnv) -and -not (Test-Path $targetEnv)) {
    Copy-Item $sourceEnv $targetEnv
    Write-Host "Copied .env (secrets remain local)." -ForegroundColor Green
} elseif (Test-Path $targetEnv) {
    Write-Host "V0.7 .env already exists; left unchanged." -ForegroundColor Yellow
}

$sourceDb = Join-Path $V06Path "data\lp_manager.sqlite3"
$targetData = Join-Path $PSScriptRoot "data"
$targetDb = Join-Path $targetData "lp_manager.sqlite3"
if ((Test-Path $sourceDb) -and -not (Test-Path $targetDb)) {
    New-Item -ItemType Directory -Path $targetData -Force | Out-Null
    Copy-Item $sourceDb $targetDb
    foreach ($suffix in @("-wal", "-shm")) {
        $extra = "$sourceDb$suffix"
        if (Test-Path $extra) { Copy-Item $extra "$targetDb$suffix" }
    }
    Write-Host "Copied V0.6 SQLite state. V0.7 migrations run automatically on startup." -ForegroundColor Green
} elseif (Test-Path $targetDb) {
    Write-Host "V0.7 database already exists; left unchanged." -ForegroundColor Yellow
}

Write-Host "Upgrade copy complete. No V0.6 files were modified." -ForegroundColor Cyan
Write-Host "Next: create/activate .venv, pip install -r requirements.txt, then python .\start_lp_manager.py"
