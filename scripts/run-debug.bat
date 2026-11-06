@echo off
rem Start the tray app with live log output in this console (Ctrl+C to quit).
cd /d "%~dp0.."
".venv\Scripts\python.exe" -m assistant --debug %*
