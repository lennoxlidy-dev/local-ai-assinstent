@echo off
rem Download llama.cpp (CUDA build), the language models (~9 GB) and the speech models (~0.7 GB). Safe to re-run: resumes.
cd /d "%~dp0.."
".venv\Scripts\python.exe" -m assistant --setup %*
pause
