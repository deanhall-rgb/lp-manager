param(
  [string]$V082Path = "C:\Users\deano\Documents\lp_manager_v0_8_2"
)
$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
Write-Host "LP Manager v0.8.3 -> v0.8.4 live-position + market-data reliability patch migration"
Write-Host "Source: $V082Path"
Write-Host "Target: $Here"
if (-not (Test-Path $V082Path)) { throw "V0.8.2 folder not found: $V082Path" }
if (Test-Path (Join-Path $V082Path ".env")) {
  Copy-Item (Join-Path $V082Path ".env") (Join-Path $Here ".env") -Force
  Write-Host "Copied .env"
}
$sourceData = Join-Path $V082Path "data"
$targetData = Join-Path $Here "data"
New-Item -ItemType Directory -Force $targetData | Out-Null
foreach($name in @("lp_manager.sqlite3","lp_manager.sqlite3-wal","lp_manager.sqlite3-shm")) {
  $src=Join-Path $sourceData $name
  if(Test-Path $src){ Copy-Item $src (Join-Path $targetData $name) -Force }
}
Write-Host "Copied V0.8.2 runtime state. V0.8.2 was not modified."
Write-Host "V0.8.4 will auto-rescan current V3 NFT ownership when it starts."
