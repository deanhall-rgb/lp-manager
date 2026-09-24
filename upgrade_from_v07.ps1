param(
    [string]$V07Path = "C:\Users\deano\Documents\lp_manager_v0_7"
)

$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

Write-Host "LP Manager V0.8 upgrade helper" -ForegroundColor Cyan
Write-Host "Source V0.7: $V07Path"

if (-not (Test-Path $V07Path)) {
    throw "V0.7 folder not found: $V07Path"
}

$sourceEnv = Join-Path $V07Path ".env"
$targetEnv = Join-Path $PSScriptRoot ".env"
if ((Test-Path $sourceEnv) -and -not (Test-Path $targetEnv)) {
    Copy-Item $sourceEnv $targetEnv
    Write-Host "Copied .env (secrets remain local)." -ForegroundColor Green
} elseif (Test-Path $targetEnv) {
    Write-Host "V0.8 .env already exists; left unchanged." -ForegroundColor Yellow
}

$sourceDb = Join-Path $V07Path "data\lp_manager.sqlite3"
$targetData = Join-Path $PSScriptRoot "data"
$targetDb = Join-Path $targetData "lp_manager.sqlite3"
if ((Test-Path $sourceDb) -and -not (Test-Path $targetDb)) {
    New-Item -ItemType Directory -Path $targetData -Force | Out-Null
    Copy-Item $sourceDb $targetDb
    foreach ($suffix in @("-wal", "-shm")) {
        $extra = "$sourceDb$suffix"
        if (Test-Path $extra) { Copy-Item $extra "$targetDb$suffix" }
    }
    Write-Host "Copied V0.7 SQLite state. V0.8 migrations run automatically on startup." -ForegroundColor Green
} elseif (Test-Path $targetDb) {
    Write-Host "V0.8 database already exists; left unchanged." -ForegroundColor Yellow
}

Write-Host "Upgrade copy complete. No V0.7 files were modified." -ForegroundColor Cyan
Write-Host "Next: create/activate .venv, pip install -r requirements.txt, then python .\start_lp_manager.py"
