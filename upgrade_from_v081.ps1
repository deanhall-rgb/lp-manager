param(
  [string]$V081Path = "C:\Users\deano\Documents\lp_manager_v0_8_1"
)
$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
Write-Host "LP Manager v0.8.1 -> v0.8.2 evidence/reliability patch migration"
Write-Host "Source: $V081Path"
Write-Host "Target: $Here"
if (-not (Test-Path $V081Path)) { throw "V0.8.1 folder not found: $V081Path" }
if (Test-Path (Join-Path $V081Path ".env")) { Copy-Item (Join-Path $V081Path ".env") (Join-Path $Here ".env") -Force; Write-Host "Copied .env" }
$sourceData = Join-Path $V081Path "data"
$targetData = Join-Path $Here "data"
New-Item -ItemType Directory -Force $targetData | Out-Null
foreach($name in @("lp_manager.sqlite3","lp_manager.sqlite3-wal","lp_manager.sqlite3-shm")) {
  $src=Join-Path $sourceData $name
  if(Test-Path $src){ Copy-Item $src (Join-Path $targetData $name) -Force }
}
Write-Host "Copied V0.8.1 runtime state. V0.8.1 was not modified."
Write-Host "V0.8.2 will re-import corrected DELTA evidence on startup."
