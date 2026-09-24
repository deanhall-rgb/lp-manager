param(
    [string]$V085Path = "C:\Users\deano\Documents\lp_manager_v0_8_5"
)
$ErrorActionPreference = "Stop"
Write-Host "Upgrading LP Manager v0.8.5 runtime state into v0.8.6 profit-engine preview..."
if (-not (Test-Path $V085Path)) { throw "Source v0.8.5 path not found: $V085Path" }
$here = Split-Path -Parent $MyInvocation.MyCommand.Path
foreach ($name in @('.env','data')) {
    $src = Join-Path $V085Path $name
    $dst = Join-Path $here $name
    if (Test-Path $src) {
        if (Test-Path $dst) { Remove-Item $dst -Recurse -Force }
        Copy-Item $src $dst -Recurse -Force
        Write-Host "Copied $name"
    }
}
Write-Host "v0.8.6 runtime migration complete. v0.8.5 was not modified."
