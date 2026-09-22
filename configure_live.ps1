$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

if (-not (Test-Path ".env")) {
    Copy-Item ".env.example" ".env"
    Write-Host "Created .env from .env.example" -ForegroundColor Green
} else {
    Write-Host ".env already exists - leaving existing values untouched." -ForegroundColor Yellow
}

Write-Host ""
Write-Host "Opening .env. Add your PUBLIC wallet address and RPC URLs for the chains you use." -ForegroundColor Cyan
Write-Host "Never add a seed phrase or wallet private key." -ForegroundColor Red
Start-Process notepad.exe (Join-Path $PSScriptRoot ".env")
