@echo off
rem Download llama.cpp (CUDA build) and the language models (~9 GB). Safe to re-run: resumes.
cd /d "%~dp0.."
".venv\Scripts\python.exe" -m assistant --setup %*
pause
