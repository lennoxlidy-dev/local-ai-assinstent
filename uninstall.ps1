<#
.SYNOPSIS
    Remove the assistant: program, shortcuts, autostart and the "Installed apps" entry.
    Your settings/memory/conversations and the downloaded models are kept unless you choose to delete them.
.EXAMPLE
    powershell -ExecutionPolicy Bypass -File uninstall.ps1            # asks what to remove
    powershell -ExecutionPolicy Bypass -File uninstall.ps1 -Quiet -RemoveData -RemoveModels
#>
param([switch]$Quiet, [switch]$RemoveData, [switch]$RemoveModels)

$ErrorActionPreference = "Continue"
$InstallDir = Split-Path -Parent $MyInvocation.MyCommand.Path
$Roaming = Join-Path $env:APPDATA "Assistant"
$Local = Join-Path $env:LOCALAPPDATA "Assistant"

function Stop-Assistant([string]$dir) {
    # Ask the running app to quit (stops llama.cpp and closes the logs cleanly) ...
    $py = Join-Path $dir ".venv\Scripts\python.exe"
    if (Test-Path $py) {
        $ErrorActionPreference = "Continue"     # (only inside this function)
        & $py -I -m assistant --quit *> $null
    }
    # ... then end whatever still runs from the folder (Python started through the venv launcher included).
    Get-CimInstance Win32_Process -ErrorAction SilentlyContinue | Where-Object {
        $_.Name -match '^(python|pythonw|assistant|assistant-cli|AssistantCLI|llama-server)\.exe$' -and (
            ($_.ExecutablePath -and $_.ExecutablePath.StartsWith($dir, [StringComparison]::OrdinalIgnoreCase)) -or
            ($_.CommandLine -and $_.CommandLine.IndexOf($dir, [StringComparison]::OrdinalIgnoreCase) -ge 0)) } |
        ForEach-Object { Stop-Process -Id $_.ProcessId -Force -ErrorAction SilentlyContinue }
}

function Ask([string]$question) {
    if ($Quiet) { return $false }
    $a = Read-Host "$question [y/N]"
    return $a -match '^(y|yes|j|ja)$'
}

if (-not $Quiet) {
    if (-not (Ask "Remove the assistant from this PC?")) { Write-Host "Nothing was removed."; exit 0 }
    if (-not $RemoveData) { $RemoveData = Ask "Also delete your settings, memory and conversations ($Roaming)?" }
    if (-not $RemoveModels -and (Test-Path (Join-Path $Local "models"))) {
        $gb = [math]::Round(((Get-ChildItem (Join-Path $Local "models") -Recurse -File -ErrorAction SilentlyContinue |
                Measure-Object Length -Sum).Sum) / 1GB, 1)
        $RemoveModels = Ask "Also delete the downloaded AI models ($gb GB) and logs ($Local)?"
    }
}

Write-Host "Closing the assistant ..."
Stop-Assistant $InstallDir
Get-Process llama-server -ErrorAction SilentlyContinue | Where-Object {
    $_.Path -and $_.Path.StartsWith((Join-Path $Local "bin"), [StringComparison]::OrdinalIgnoreCase) } |
    Stop-Process -Force -ErrorAction SilentlyContinue
Start-Sleep -Seconds 1

Write-Host "Removing autostart, shortcuts and the uninstall entry ..."
Remove-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Run" -Name "Assistant" -ErrorAction SilentlyContinue
Remove-ItemProperty -Path "HKCU:\Software\Microsoft\Windows\CurrentVersion\Explorer\StartupApproved\Run" -Name "Assistant" -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force (Join-Path ([Environment]::GetFolderPath("Programs")) "Assistant") -ErrorAction SilentlyContinue
Remove-Item -Force (Join-Path ([Environment]::GetFolderPath("Desktop")) "Assistant.lnk") -ErrorAction SilentlyContinue
Remove-Item -Recurse -Force "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Assistant" -ErrorAction SilentlyContinue

if ($RemoveData) { Write-Host "Deleting settings and data ..."; Remove-Item -Recurse -Force $Roaming -ErrorAction SilentlyContinue }
if ($RemoveModels) { Write-Host "Deleting models and logs ..."; Remove-Item -Recurse -Force $Local -ErrorAction SilentlyContinue }

Write-Host "Removing the program folder ..."
Set-Location $env:TEMP
# this script lives inside the folder: delete it a moment after we have exited
Start-Process -WindowStyle Hidden -FilePath "cmd.exe" -ArgumentList "/c ping -n 3 127.0.0.1 >nul & rmdir /s /q `"$InstallDir`""
Write-Host "The assistant was removed." -ForegroundColor Green
if (-not $RemoveData) { Write-Host "Your settings and memory are still in $Roaming (delete that folder to remove them)." }
if (-not $Quiet) { Read-Host "Press Enter to close" | Out-Null }
