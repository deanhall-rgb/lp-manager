param(
  [string]$V083Path = "C:\Users\deano\Documents\lp_manager_v0_8_3"
)
$ErrorActionPreference = "Stop"
$Here = Split-Path -Parent $MyInvocation.MyCommand.Path
Write-Host "LP Manager v0.8.3 -> v0.8.4 reliability patch migration"
Write-Host "Source: $V083Path"
Write-Host "Target: $Here"
if (-not (Test-Path $V083Path)) { throw "V0.8.3 folder not found: $V083Path" }
if (Test-Path (Join-Path $V083Path ".env")) {
  Copy-Item (Join-Path $V083Path ".env") (Join-Path $Here ".env") -Force
  Write-Host "Copied .env"
}
$sourceData = Join-Path $V083Path "data"
$targetData = Join-Path $Here "data"
New-Item -ItemType Directory -Force $targetData | Out-Null
foreach($name in @("lp_manager.sqlite3","lp_manager.sqlite3-wal","lp_manager.sqlite3-shm")) {
  $src=Join-Path $sourceData $name
  if(Test-Path $src){ Copy-Item $src (Join-Path $targetData $name) -Force }
}
Write-Host "Copied V0.8.3 runtime state. V0.8.3 was not modified."
Write-Host "V0.8.4 will rescan current V3 ownership using corrected Robinhood Blockscout + Alchemy ownership discovery."
