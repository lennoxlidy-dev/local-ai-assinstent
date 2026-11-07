@echo off
rem Double-click to install or update the assistant (per user, no administrator rights needed).
rem Options are passed through, e.g.:  install.bat -SkipModels -DesktopShortcut
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0install.ps1" %*
