<#
.SYNOPSIS
    One-click install (or update) of the local voice assistant - per user, no administrator rights.

.DESCRIPTION
    1. copies the app to %LOCALAPPDATA%\Programs\Assistant (or -InstallDir)
    2. installs uv (Python manager) if needed, then Python 3.14 + all packages into a private .venv
    3. downloads llama.cpp, the language model that suits your graphics card and the speech models
       (-SkipModels: the setup wizard downloads them in the background instead; -AllModels: also the other model)
    4. creates Start menu shortcuts and an "Installed apps" entry (with uninstall), then starts the assistant
    Run it again to update - settings, memory, conversations and models are kept.

.EXAMPLE
    powershell -ExecutionPolicy Bypass -File install.ps1
    powershell -ExecutionPolicy Bypass -File install.ps1 -SkipModels -DesktopShortcut
#>
param(
    [string]$InstallDir = (Join-Path $env:LOCALAPPDATA "Programs\Assistant"),
    [switch]$SkipModels,
    [switch]$AllModels,
    [switch]$DesktopShortcut,
    [switch]$NoLaunch,
    [switch]$NoPause
)

$ErrorActionPreference = "Stop"
$ProgressPreference = "SilentlyContinue"
$Source = Split-Path -Parent $MyInvocation.MyCommand.Path
$LogFile = Join-Path $env:TEMP "assistant-install.log"
try { Start-Transcript -Path $LogFile -Force | Out-Null } catch { }

function Step([int]$n, [string]$text) {
    Write-Host ""
    Write-Host "[$n/6] $text" -ForegroundColor Cyan
}

function Finish([int]$code) {
    try { Stop-Transcript | Out-Null } catch { }
    if (-not $NoPause) { Write-Host ""; Read-Host "Press Enter to close" | Out-Null }
    exit $code
}

function Fail([string]$text) {
    Write-Host ""
    Write-Host "Installation failed: $text" -ForegroundColor Red
    Write-Host "Full log: $LogFile"
    Write-Host "See README.md, section Troubleshooting."
    Finish 1
}

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

Write-Host "Assistant - local voice assistant for Windows" -ForegroundColor White
Write-Host "Install folder: $InstallDir"

# ------------------------------------------------------------------ 1. checks
Step 1 "Checking this PC"
$os = [Environment]::OSVersion.Version
if ($os.Major -lt 10 -or $os.Build -lt 17763) { Fail "Windows 10 (1809) or Windows 11 is needed." }
if (-not [Environment]::Is64BitOperatingSystem) { Fail "64-bit Windows is needed." }
$needGB = 3
if (-not $SkipModels) { $needGB = 11 }
if ($AllModels) { $needGB = 15 }
$dataDrive = (Split-Path -Qualifier $env:LOCALAPPDATA).TrimEnd(':')
$freeGB = [math]::Round((Get-PSDrive $dataDrive).Free / 1GB, 1)
Write-Host "  Windows $($os.Major).0 build $($os.Build), $freeGB GB free on ${dataDrive}:"
if ($freeGB -lt $needGB) { Fail "Not enough disk space on ${dataDrive}: - about $needGB GB are needed, $freeGB GB are free." }
try {
    $gpu = (Get-CimInstance Win32_VideoController | Where-Object { $_.Name -match "NVIDIA" } | Select-Object -First 1).Name
    if ($gpu) { Write-Host "  Graphics: $gpu" } else { Write-Host "  No NVIDIA graphics card found - the language model will run on the CPU (slower)." -ForegroundColor Yellow }
} catch { }

# ------------------------------------------------------------------ 2. files
Step 2 "Copying the app"
if (Test-Path (Join-Path $InstallDir ".venv")) {
    Write-Host "  Closing the assistant if it is running (update) ..."
    Stop-Assistant $InstallDir
    Start-Sleep -Seconds 1
}
$same = (Resolve-Path $Source).Path.TrimEnd('\') -ieq $InstallDir.TrimEnd('\')
if (-not $same) {
    New-Item -ItemType Directory -Force -Path $InstallDir | Out-Null
    $null = robocopy (Join-Path $Source "src") (Join-Path $InstallDir "src") /MIR /XD __pycache__ /NFL /NDL /NJH /NJS /NP
    if ($LASTEXITCODE -ge 8) { Fail "copying the program files failed (robocopy $LASTEXITCODE)" }
    foreach ($dir in "scripts", "bench", "packaging") {
        if (Test-Path (Join-Path $Source $dir)) {
            $null = robocopy (Join-Path $Source $dir) (Join-Path $InstallDir $dir) /MIR /XD __pycache__ /NFL /NDL /NJH /NJS /NP
            if ($LASTEXITCODE -ge 8) { Fail "copying $dir failed (robocopy $LASTEXITCODE)" }
        }
    }
    foreach ($f in "pyproject.toml", "uv.lock", "README.md", "PROGRESS.md", "SPEC.md", "install.ps1", "install.bat",
                   "uninstall.ps1", ".python-version") {
        if (Test-Path (Join-Path $Source $f)) { Copy-Item (Join-Path $Source $f) $InstallDir -Force }
    }
}
Write-Host "  done"

# ------------------------------------------------------------------ 3. uv + Python + packages
Step 3 "Installing Python and the packages (first time: a few minutes)"
$uv = Get-Command uv -ErrorAction SilentlyContinue
if (-not $uv) {
    Write-Host "  Installing uv (Python package manager, ~30 MB) ..."
    try {
        powershell -NoProfile -ExecutionPolicy Bypass -Command "irm https://astral.sh/uv/install.ps1 | iex" | Out-Host
    } catch { Fail "uv could not be installed: $_" }
    $env:Path = "$env:USERPROFILE\.local\bin;$env:Path"
    $uv = Get-Command uv -ErrorAction SilentlyContinue
    if (-not $uv) { Fail "uv was installed but is not on PATH - open a new window and run the installer again." }
}
Push-Location $InstallDir
try {
    uv sync --frozen --no-dev --python 3.14 | Out-Host
    if ($LASTEXITCODE -ne 0) { Fail "installing the Python packages failed (uv sync exit code $LASTEXITCODE)" }
} finally { Pop-Location }
$py = Join-Path $InstallDir ".venv\Scripts\python.exe"
$exe = Join-Path $InstallDir ".venv\Scripts\assistant.exe"
if (-not (Test-Path $exe)) { $exe = Join-Path $InstallDir ".venv\Scripts\pythonw.exe" }
& $py -I -m assistant --version | Out-Host
if ($LASTEXITCODE -ne 0) { Fail "the installed app does not start (python -m assistant --version failed)" }

# ------------------------------------------------------------------ 4. models
Step 4 "Downloading llama.cpp and the AI models"
if ($SkipModels) {
    Write-Host "  skipped - the setup wizard downloads them in the background on the first start"
} else {
    $modelArg = "auto"
    if ($AllModels) { $modelArg = "" }
    $setupArgs = @("-I", "-m", "assistant", "--setup")
    if ($modelArg) { $setupArgs += @("--models", $modelArg) }
    & $py @setupArgs
    if ($LASTEXITCODE -ne 0) {
        Write-Host "  Some downloads did not finish. That's OK: the assistant retries them on its own" -ForegroundColor Yellow
        Write-Host "  (or run scripts\setup-models.bat later)." -ForegroundColor Yellow
    }
}

# ------------------------------------------------------------------ 5. shortcuts + uninstall entry
Step 5 "Creating shortcuts"
$icon = Join-Path $InstallDir "packaging\assistant.ico"
$shell = New-Object -ComObject WScript.Shell
function New-Shortcut([string]$path, [string]$target, [string]$arguments, [string]$description) {
    $s = $shell.CreateShortcut($path)
    $s.TargetPath = $target
    $s.Arguments = $arguments
    $s.WorkingDirectory = $InstallDir
    if (Test-Path $icon) { $s.IconLocation = $icon }
    $s.Description = $description
    $s.Save()
}
$menu = Join-Path ([Environment]::GetFolderPath("Programs")) "Assistant"
New-Item -ItemType Directory -Force -Path $menu | Out-Null
$launchArgs = ""
if ($exe.EndsWith("pythonw.exe")) { $launchArgs = "-I -m assistant" }
New-Shortcut (Join-Path $menu "Assistant.lnk") $exe $launchArgs "Local voice assistant (starting it again opens the settings)"
New-Shortcut (Join-Path $menu "Assistant with log window.lnk") (Join-Path $InstallDir "scripts\run-debug.bat") "" "Start with a live log (troubleshooting)"
New-Shortcut (Join-Path $menu "Assistant command line.lnk") (Join-Path $InstallDir "scripts\run-cli.bat") "" "Text-mode debug CLI"
New-Shortcut (Join-Path $menu "Uninstall Assistant.lnk") "powershell.exe" "-NoProfile -ExecutionPolicy Bypass -File `"$InstallDir\uninstall.ps1`"" "Remove the assistant"
if ($DesktopShortcut) {
    New-Shortcut (Join-Path ([Environment]::GetFolderPath("Desktop")) "Assistant.lnk") $exe $launchArgs "Local voice assistant"
}
$version = "0.0.0"
$m = Select-String -Path (Join-Path $InstallDir "pyproject.toml") -Pattern '^version\s*=\s*"([^"]+)"' | Select-Object -First 1
if ($m) { $version = $m.Matches[0].Groups[1].Value }
$key = "HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\Assistant"
New-Item -Path $key -Force | Out-Null
$sizeKB = [int]((Get-ChildItem $InstallDir -Recurse -File -ErrorAction SilentlyContinue | Measure-Object Length -Sum).Sum / 1KB)
$props = @{
    DisplayName = "Assistant (local voice assistant)"; DisplayVersion = $version; Publisher = "Assistant"
    InstallLocation = $InstallDir; DisplayIcon = $icon; EstimatedSize = $sizeKB; NoModify = 1; NoRepair = 1
    UninstallString = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$InstallDir\uninstall.ps1`""
}
foreach ($k in $props.Keys) {
    $type = "String"
    if ($props[$k] -is [int]) { $type = "DWord" }
    New-ItemProperty -Path $key -Name $k -Value $props[$k] -PropertyType $type -Force | Out-Null
}
Write-Host "  Start menu: Assistant"

# ------------------------------------------------------------------ 6. start
Step 6 "Done"
Write-Host "  The assistant lives in the tray (bottom right, maybe under ^)."
Write-Host "  On the first start a short setup asks for its name, your voice and a few preferences."
if (-not $NoLaunch) {
    $ErrorActionPreference = "Continue"
    & $py -I -m assistant --quit *> $null     # a copy started elsewhere (e.g. from the download folder)
    $ErrorActionPreference = "Stop"
    # (an empty -ArgumentList is an error in Windows PowerShell 5.1)
    if ($launchArgs) { Start-Process -FilePath $exe -ArgumentList $launchArgs -WorkingDirectory $InstallDir }
    else { Start-Process -FilePath $exe -WorkingDirectory $InstallDir }
    Write-Host "  Started."
}
Finish 0
