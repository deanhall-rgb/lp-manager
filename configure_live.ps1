$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example" -ForegroundColor Green
} else {
    Write-Host ".env already exists - existing values were not overwritten." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "V0.8 needs your PUBLIC wallet address. RPC URLs are optional because read-only public fallbacks exist." -ForegroundColor Cyan
Write-Host "For reliable multi-chain wallet/NFT discovery, add one ALCHEMY_API_KEY or explicit RPC URLs." -ForegroundColor Cyan
Write-Host "Add OPENAI_API_KEY only if you want live AI investment memos; deterministic analysis works without it." -ForegroundColor Cyan
Write-Host "Never add a seed phrase or wallet private key." -ForegroundColor Red
Start-Process notepad.exe (Join-Path $PSScriptRoot ".env")
