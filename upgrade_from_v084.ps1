param(
    [string]$V084Path = "C:\Users\deano\Documents\lp_manager_v0_8_4"
)

$ErrorActionPreference = "Stop"
$here = Split-Path -Parent $MyInvocation.MyCommand.Path

Write-Host "Upgrading LP Manager v0.8.4 runtime state into v0.8.5..."

$oldEnv = Join-Path $V084Path ".env"
if (Test-Path $oldEnv) {
    Copy-Item $oldEnv (Join-Path $here ".env") -Force
    Write-Host "Copied .env"
} else {
    Write-Warning "No .env found at $oldEnv"
}

$oldDb = Join-Path $V084Path "data\lp_manager.sqlite3"
$newData = Join-Path $here "data"
New-Item -ItemType Directory -Force $newData | Out-Null
if (Test-Path $oldDb) {
    Copy-Item $oldDb (Join-Path $newData "lp_manager.sqlite3") -Force
    Write-Host "Copied SQLite state"
} else {
    Write-Warning "No SQLite database found at $oldDb"
}

Write-Host "v0.8.5 runtime migration complete. v0.8.4 was not modified."
