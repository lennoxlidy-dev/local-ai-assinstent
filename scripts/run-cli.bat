@echo off
rem Text-mode debug CLI.
cd /d "%~dp0.."
".venv\Scripts\python.exe" -m assistant --cli %*
