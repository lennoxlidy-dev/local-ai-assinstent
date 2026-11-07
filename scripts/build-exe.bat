@echo off
rem Build dist\Assistant\Assistant.exe with PyInstaller (see build-exe.ps1).
rem Options are passed through, e.g.:  build-exe.bat -Zip
powershell -NoProfile -ExecutionPolicy Bypass -File "%~dp0build-exe.ps1" %*
pause
