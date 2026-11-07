<#
.SYNOPSIS
    Build the standalone program folder dist\Assistant\ (Assistant.exe + AssistantCLI.exe) with PyInstaller.
.DESCRIPTION
    No Python is needed on the target PC; the setup wizard downloads the models on the first start.
    After the build, "AssistantCLI.exe --self-test" opens every window once (with a temporary data
    folder, nothing is downloaded or changed) to make sure no file is missing from the build.
    The program is not code-signed: Windows SmartScreen may warn ("More info" -> "Run anyway").
.PARAMETER Zip
    Also pack the folder into dist\Assistant-<version>.zip to share it.
.PARAMETER SkipTest
    Skip the self-test.
#>
param([switch]$Zip, [switch]$SkipTest)
$ErrorActionPreference = "Stop"
Set-Location (Split-Path $PSScriptRoot -Parent)
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { throw "uv is missing - run scripts\dev-setup.ps1 first" }
uv sync --frozen --group build
if ($LASTEXITCODE -ne 0) { throw "uv sync failed" }
uv run --frozen --no-sync pyinstaller packaging\assistant.spec --noconfirm --clean
if ($LASTEXITCODE -ne 0) { throw "PyInstaller failed" }
$cli = "dist\Assistant\AssistantCLI.exe"
$version = ((& $cli --version) -split " ")[-1]
if ($LASTEXITCODE -ne 0) { throw "the built program does not start" }
if (-not $SkipTest) {
    Write-Host ""
    Write-Host "Self-test (the windows open briefly) ..."
    & $cli --self-test
    if ($LASTEXITCODE -ne 0) { throw "the self-test found problems (see above)" }
}
if ($Zip) {
    $zipPath = "dist\Assistant-$version.zip"
    if (Test-Path $zipPath) { Remove-Item $zipPath }
    Compress-Archive -Path "dist\Assistant" -DestinationPath $zipPath
    Write-Host "Zipped: $zipPath"
}
Write-Host ""
Write-Host "Built version ${version}: dist\Assistant\Assistant.exe (tray app) and AssistantCLI.exe (console tools)" -ForegroundColor Green
