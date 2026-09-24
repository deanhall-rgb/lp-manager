param(
  [string]$V08Path = "C:\Users\deano\Documents\lp_manager_v0_8"
)
$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
Write-Host "LP Manager v0.8 -> v0.8.1 patch migration"
Write-Host "Source: $V08Path"
Write-Host "Target: $Here"
if (-not (Test-Path $V08Path)) { throw "V0.8 folder not found: $V08Path" }
if (Test-Path (Join-Path $V08Path ".env")) { Copy-Item (Join-Path $V08Path ".env") (Join-Path $Here ".env") -Force; Write-Host "Copied .env" }
$sourceData = Join-Path $V08Path "data"
$targetData = Join-Path $Here "data"
New-Item -ItemType Directory -Force $targetData | Out-Null
foreach($name in @("lp_manager.sqlite3","lp_manager.sqlite3-wal","lp_manager.sqlite3-shm")) {
  $src=Join-Path $sourceData $name
  if(Test-Path $src){ Copy-Item $src (Join-Path $targetData $name) -Force }
}
Write-Host "Copied V0.8 runtime state. V0.8 was not modified."
Write-Host "V0.8.1 will reconcile the bundled DELTA reconstruction on startup."
