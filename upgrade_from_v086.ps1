param(
    [string]$V086Path = "C:\\Users\\deano\\Documents\\lp_manager_v0_8_6"
)

$ErrorActionPreference = "Stop"
Write-Host "Upgrading LP Manager v0.8.6 runtime state into v0.8.7 profitability correction release..."

if (-not (Test-Path $V086Path)) {
    throw "Source v0.8.6 path not found: $V086Path"
}

$here = Split-Path -Parent $MyInvocation.MyCommand.Path

foreach ($name in @('.env','data')) {
    $src = Join-Path $V086Path $name
    $dst = Join-Path $here $name
    if (Test-Path $src) {
        if (Test-Path $dst) { Remove-Item $dst -Recurse -Force }
        Copy-Item $src $dst -Recurse -Force
        Write-Host "Copied $name"
    }
}

# V0.8.7 is GBP-first. Preserve every existing setting/key but normalise the
# display currency so copied v0.8.6 state cannot silently reopen in USD.
$envFile = Join-Path $here ".env"
if (Test-Path $envFile) {
    $text = Get-Content $envFile -Raw
    if ($text -match "(?m)^LP_MANAGER_CURRENCY=") {
        $text = [regex]::Replace($text, "(?m)^LP_MANAGER_CURRENCY=.*$", "LP_MANAGER_CURRENCY=GBP")
    } else {
        $text = "LP_MANAGER_CURRENCY=GBP`r`n" + $text
    }
    Set-Content -Path $envFile -Value $text -Encoding UTF8
    Write-Host "Set LP_MANAGER_CURRENCY=GBP"
}

Write-Host "v0.8.7 runtime migration complete. v0.8.6 was not modified."
Write-Host "Next: create/activate .venv, install requirements.txt, then run start_lp_manager.py"
