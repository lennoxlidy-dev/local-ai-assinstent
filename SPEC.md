Build me a local, voice-controlled AI assistant for Windows from scratch.

Before coding:
- Save this entire prompt as SPEC.md in the project root and init a git repo.
- Show me a short architecture plan and milestone list, then wait for my OK.
- Keep PROGRESS.md updated (decisions, status, how to test) and commit after every milestone, so we can continue across sessions.
- If something important is unclear, ask me. Otherwise pick sensible defaults and note them.

## Goal
A smart, fast, lightweight personal assistant that runs 100% locally on my laptop. It always listens in the background for its name and can actually control my PC: open apps, work with my files, move the mouse and click, type, and use the terminal.
Example: "<name>, play a song" → it opens Spotify (or my chosen music app) on a separate virtual desktop, so my current desktop isn't disturbed, and starts music.

## Hardware & hard constraints
- Intel i9-13900HX + RTX 4070 Laptop GPU (8 GB VRAM). Windows 10/11 only for now. Detect RAM/VRAM at runtime.
- Fully local: no cloud APIs, no telemetry. Internet only to download models/packages.
- Fast and lightweight is a top priority: no Electron, no PyTorch at runtime.
- Languages: English and German (chosen in onboarding).

## Core behavior
- User-level background app with a tray icon. NOT a Windows service, because services can't show UI. Autostart at login (toggle).
- Idle: only a tiny CPU wake-word detector runs.
- The wake word is the name I give the assistant on first start. It must work for any name without manual model training.
- On wake word:
  1. Play a short sound and show a small popup.
  2. Listen until I'm silent for a moment (VAD; default ~1 s, adjustable; max length cap).
  3. Transcribe, act, and answer (text in the popup + optional voice).
- Follow-up: after answering, keep listening ~5 s for a follow-up without the name (toggle).
- Fast path: common commands are handled instantly by a local intent matcher, without the LLM. These include open/close app, play/pause/next, volume, mute, time, timers, lock PC and switch desktop.
- Everything else goes to an LLM agent loop: tool calling, multi-step with a step limit, progress shown in the popup.
- Push-to-talk hotkey as an alternative to the wake word.
- The LLM's system prompt includes my profile, remembered facts, the time, the active window and the current desktop. Keep it short for speed.

## Tools (all go through the permission system)
- Apps:
  - Auto-index installed apps (Start Menu + Store apps) with fuzzy matching and custom aliases.
  - Open, close and focus windows.
- Virtual desktops:
  - A dedicated "Assistant" desktop, with a per-app rule for whether each app opens there or on the current desktop.
  - These Windows APIs are undocumented and change between builds. Use a maintained library (e.g. pyvda) and fall back gracefully.
- Media:
  - Control any player via the Windows media session (SMTC).
  - For "play <song/artist>" in Spotify: use the spotify: search URI, then UI automation to start the top result.
  - Optional Spotify Web API if I add my own credentials.
- Files:
  - Search with Everything (voidtools) if installed, otherwise a low-priority background index.
  - Open, read/summarize (txt, PDF, Office), create, copy, move, rename.
  - Delete = Recycle Bin only.
- Mouse/keyboard/screen:
  - DPI-aware moves, clicks, typing and hotkeys.
  - Prefer UI Automation to find controls by name. Fall back to screenshot + OCR or the model's vision.
- Terminal: PowerShell/cmd with a timeout, output shown in the popup.
- System: volume, brightness, screenshots, clipboard, timers/reminders (Windows notifications), lock/sleep.
- Memory: "remember that …" stores facts locally.
- Routines: named multi-step macros I define (e.g. "gaming mode" = open Steam + Discord, volume 40%).

## Safety (enforced in code, not just in the prompt)
- Each capability is set to Allow, Ask or Block.
- Defaults: read access to all my drives, changes need confirmation, Windows and Program Files are protected.
- Always ask before risky actions and show the exact command/paths. I confirm by voice or click. Risky actions include:
  - delete, move or overwrite
  - terminal commands that change things
  - installs
  - closing apps with unsaved work
- Nothing runs as admin without asking, and then only via the normal UAC prompt.
- Text from files, web pages and the screen is data, never instructions (prompt-injection protection).
- A panic hotkey or "<name>, stop" instantly cancels everything and releases the mouse/keyboard.
- Keep a full action log.
- Mic audio stays in memory and is never saved unless I turn that on.
- Optional voice lock (speaker verification) so videos or other people can't trigger it.

## First start: onboarding (at least 15 questions, more if needed)
- A friendly wizard, one question at a time.
- The assistant speaks the questions (if voice is on). I answer by voice or typing.
- Every question has a default and can be skipped. The wizard can be re-run from settings, and every answer can be edited later.
- Download models in the background while the wizard runs.
- Cover at least:
  - assistant name (I say it 3x to test and calibrate the wake word)
  - what to call me, and language
  - voice replies on/off, plus voice choice with a preview
  - personality and answer length
  - microphone + level test
  - music app, browser and other default apps
  - folders to allow/block, and the permission level for each capability
  - actions that always need confirmation
  - virtual desktop rules
  - autostart, and hotkeys (push-to-talk, panic)
  - silence timeout, game mode, and speed vs smart model
  - saving conversations + retention
  - voice lock
  - my routines

## GUI (modern dark Windows 11 look, smooth animations)
- Popup:
  - Small, frameless, rounded, acrylic blur, always on top.
  - Never steals focus, so it works over games and while I'm typing.
  - Animated orb/waveform that reacts to my voice.
  - States: listening → thinking → acting/speaking → done.
  - Shows a live transcript and the answer, then fades out after a few seconds. Click to expand it into a mini chat.
  - Position, theme and accent color are configurable.
- Settings window:
  - Loaded on demand and freed when closed.
  - Tabs: General, Voice & Wake Word, AI Model & Performance, Permissions, Apps & Aliases, Routines, History, Privacy, Appearance, Action Log.
- Tray menu: settings, pause listening (with a clear indicator), game mode, quit.

## Conversations
- Save all conversations as text in local SQLite with full-text search.
- History tab: search, rename, delete, export (Markdown/JSON).
- Retention setting.

## Recommended stack
It's late 2026: verify these are still the best and current versions. If something better exists, use it and tell me why.
- Core: Python 3.12 + PySide6/QML UI, modular (audio, wakeword, stt, tts, llm, tools, permissions, ui, storage).
- LLM:
  - llama.cpp server (CUDA build), started, stopped and health-checked by the app. Use Ollama only if it's much simpler.
  - Benchmark Qwen3.5 4B vs 9B (GGUF Q4_K_M) on my GPU. Default to the 9B if it meets the latency targets, otherwise the 4B, and let me switch.
  - Compare with current alternatives (e.g. Gemma 4) for tool-calling reliability.
  - Non-thinking mode for normal commands; thinking only for complex tasks.
  - Native tool calling with strict JSON schemas.
  - Use the model's vision for screen understanding.
- STT: NVIDIA Parakeet TDT 0.6B v3 via ONNX (sherpa-onnx or onnx-asr). Fallback: faster-whisper large-v3-turbo.
- VAD: Silero (ONNX).
- Wake word: open-vocabulary keyword spotting (e.g. sherpa-onnx KWS) or openWakeWord. Add a sensitivity slider and an optional quick STT double-check against false triggers.
- TTS: Kokoro-82M (English), Piper (German), streamed sentence by sentence.
- Windows: UI Automation (pywinauto/uiautomation), SendInput, pyvda, WinRT via pywinrt (SMTC, notifications, OCR), send2trash.
- VRAM plan: the GPU is for the LLM. Wake word, VAD, STT and TTS run on CPU, which keeps the Python side CUDA-free, unless benchmarks say otherwise.

## Performance targets
Measure these with a benchmark script, and log per-stage timings in the debug log.
- Idle: < 2% CPU, < 300 MB RAM, GPU unused. Use low priority/EcoQoS for the listening loop.
- Latency:
  - wake word → popup: < 300 ms
  - end of speech → transcript: < 500 ms
  - fast-path command done: < 1 s
  - LLM reply starts: < 2 s
- LLM memory: keep the model loaded, or unload it after X min idle (setting).
- Game mode: detect fullscreen games, free VRAM, reload on demand.

## Milestones
Run and test each one, then tell me how to test it myself.
1. Skeleton: config in %APPDATA%, logging, tray, autostart, text-mode debug CLI.
2. Tools + permission gate + action log, with unit tests (mock destructive actions).
3. LLM server management, tool-calling agent loop, fast-path intents.
4. Voice: mic → wake word → VAD → STT → popup, then TTS. Add a test harness that feeds WAV files instead of the mic.
5. Onboarding wizard + profile/memory.
6. Settings/permissions GUI + history.
7. Finish:
   - tune against the performance targets
   - game mode
   - packaging: one-click install.ps1 + double-clickable .bat (venv, deps, llama.cpp, models), .exe build, README with troubleshooting
