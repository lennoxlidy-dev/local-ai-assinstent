# Developer setup (Windows): installs uv if missing, then Python 3.14 + all dependencies into .venv.
# Usage (PowerShell, from anywhere):   powershell -ExecutionPolicy Bypass -File scripts\dev-setup.ps1
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)

if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    Write-Host "Installing uv (Python package manager, ~30 MB)..."
    powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex"
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
}

uv sync
if ($LASTEXITCODE -ne 0) { throw "uv sync failed" }

Write-Host ""
Write-Host "Done. Next:"
Write-Host "  scripts\run-tray.bat      start the tray app (no console)"
Write-Host "  scripts\run-cli.bat       text-mode debug CLI"
Write-Host "  uv run pytest             run the tests"
