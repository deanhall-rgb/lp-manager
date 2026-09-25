param(
    [string]$V0810Path = "C:\Users\deano\Documents\lp_manager_v0_8_10"
)

$ErrorActionPreference = "Stop"
Write-Host "Upgrading LP Manager v0.8.10 runtime state into v0.8.11..."

if (-not (Test-Path $V0810Path)) {
    throw "Source v0.8.10 path not found: $V0810Path"
}

$here = Split-Path -Parent $MyInvocation.MyCommand.Path

foreach ($name in @('.env','data')) {
    $src = Join-Path $V0810Path $name
    $dst = Join-Path $here $name
    if (Test-Path $src) {
        if (Test-Path $dst) { Remove-Item $dst -Recurse -Force }
        Copy-Item $src $dst -Recurse -Force
        Write-Host "Copied $name"
    }
}

$envFile = Join-Path $here ".env"
if (Test-Path $envFile) {
    $text = Get-Content $envFile -Raw
    if ($text -match "(?m)^LP_MANAGER_CURRENCY=") {
        $text = [regex]::Replace($text, "(?m)^LP_MANAGER_CURRENCY=.*$", "LP_MANAGER_CURRENCY=GBP")
    } else {
        $text = "LP_MANAGER_CURRENCY=GBP`r`n" + $text
    }
    Set-Content -Path $envFile -Value $text -Encoding UTF8
}

Get-ChildItem -Path $here -Directory -Filter "__pycache__" -Recurse -ErrorAction SilentlyContinue |
    Remove-Item -Recurse -Force -ErrorAction SilentlyContinue

Write-Host "v0.8.11 runtime migration complete. The v0.8.10 folder was not modified."
Write-Host "Next: create/activate .venv, install requirements.txt, then run start_lp_manager.py"
