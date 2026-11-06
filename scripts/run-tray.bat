@echo off
rem Start the tray app without a console window.
cd /d "%~dp0.."
if not exist ".venv\Scripts\pythonw.exe" (
  echo Run scripts\dev-setup.ps1 first.
  pause
  exit /b 1
)
start "" ".venv\Scripts\pythonw.exe" -m assistant %*
