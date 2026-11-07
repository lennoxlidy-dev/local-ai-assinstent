@echo off
rem Measure the assistant against the performance targets (idle, wake word, speech, fast path).
rem Examples:  perf-bench.bat --llm                        also the language model (first-token latency)
rem            perf-bench.bat --attach --idle-seconds 120  idle CPU/RAM of the running tray app
cd /d "%~dp0.."
".venv\Scripts\python.exe" bench\perf_bench.py %*
pause
