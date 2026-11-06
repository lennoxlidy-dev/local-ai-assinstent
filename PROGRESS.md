# PROGRESS

Living status file. Read this first when continuing in a new session (together with `SPEC.md`).

## Milestones

| # | Milestone | Status |
|---|-----------|--------|
| 1 | Skeleton: config in %APPDATA%, logging, tray, autostart, text-mode debug CLI | ✅ done (tested on Linux; Windows test pending by user) |
| 2 | Tools + permission gate + action log, unit tests with mocked destructive actions | ⏳ next |
| 3 | LLM server management (llama.cpp), tool-calling agent loop, fast-path intents | — |
| 4 | Voice: mic → wake word → VAD → STT → popup, then TTS; WAV test harness | — |
| 5 | Onboarding wizard + profile/memory | — |
| 6 | Settings/permissions GUI + history | — |
| 7 | Performance tuning, game mode, packaging (install.ps1/.bat/.exe), README | — |

## Architecture (as built so far)

```
src/assistant/
  __main__.py          entry point: tray (default) | --cli | --debug | --smoke-test N | --home DIR
  cli.py               text-mode debug CLI (":" commands; free text -> pipeline from M3)
  core/
    paths.py           %APPDATA%\Assistant (config, db) + %LOCALAPPDATA%\Assistant (models, bin, logs, cache)
    config.py          typed dataclass config <-> TOML; validation, atomic save, unknown keys preserved
    context.py         bootstrap(): paths + logging + config + bus + state -> AppContext
    logging_setup.py   queue-based logging, <name>.log + <name>-debug.log + crash.log (faulthandler)
    events.py          thread-safe pub/sub EventBus (core is Qt-free; UI bridges via signals)
    state.py           observable runtime state (paused, game_mode, phase, voice_ready, llm_ready)
    timing.py          Trace marks/spans -> "TIMING trace=N ..." lines in the debug log
    cancel.py          CancelToken tree (panic / "stop" cancels everything + runs release callbacks)
    autostart.py       HKCU Run key, respects/clears Task Manager "disabled" flag
    hardware.py        RAM/CPU (psutil, registry) + NVIDIA VRAM via NVML (ctypes) -> nvidia-smi fallback
    proc.py            run_hidden(): subprocesses with CREATE_NO_WINDOW (no console flashes)
  win/shell.py         AppUserModelID, per-monitor DPI awareness (CLI), open_path()
  ui/
    app.py             QApplication bootstrap, theme, config file watcher, single instance
    tray.py            tray icon + menu (settings, pause, game mode, start at login, folders, quit)
    icons.py           orb icons drawn at runtime (accent color, paused bars, game-mode badge)
    single_instance.py QLockFile + per-user QLocalServer (2nd launch -> "show" to the 1st)
tests/                 pytest (27 tests in M1)
scripts/               dev-setup.ps1, run-tray.bat, run-debug.bat, run-cli.bat
```

## Decisions & defaults (with reasons)

Stack verification (Nov 2026, checked against PyPI with Windows-x64 wheel resolution):

- **Python 3.14** (spec said 3.12): 3.12 and 3.13 are security-only now, so python.org no longer ships
  Windows installers for their fixes; 3.14 is the current bugfix line, has the incremental GC (shorter
  pauses for the audio thread), and *every* dependency we need has Windows wheels for it (PySide6,
  numpy, sherpa-onnx, pywin32, comtypes, pyvda, pycaw, uiautomation, winrt-*, pypdfium2, mss...).
  Code stays compatible with 3.12+ (`requires-python = ">=3.12"`).
- **uv** manages Python + the venv (`uv sync` installs the right Python automatically; fast, one exe).
  Also the planned base for the one-click installer (M7).
- **PySide6-Essentials 6.12** instead of full PySide6: same QtCore/Gui/Widgets/Network/Qml/Quick, without
  WebEngine/Multimedia/3D (hundreds of MB smaller).
- Resolved current versions for later milestones: sherpa-onnx 2.0.1, numpy 2.5, psutil 7.2, pyvda 0.6,
  pycaw 2026.9, uiautomation 2.0, comtypes 1.4, pywin32 312, winrt-* 10.0.28000 (pywinrt 4.1), mss 10.2,
  pypdfium2 5.15, send2trash 2.1. (Model choices are verified in M3/M4.)

Design decisions:

- **Config** = TOML (human-editable) validated by dataclasses (no pydantic: lighter, faster import).
  Invalid values -> default + warning (tray notification); broken file -> moved aside, defaults used.
  Settings UI (M6) and onboarding (M5) use the same dotted-key API (`store.set("voice.follow_up", False)`).
- **Permissions** will live in a separate `permissions.toml` (M2) so they can be protected from
  tool writes independently of normal settings.
- **Models in %LOCALAPPDATA%**, not %APPDATA% (roaming): GBs must never roam.
- **Logs**: tray -> `assistant.log`/`assistant-debug.log`, CLI -> `cli.log`/`cli-debug.log` (two processes
  can't share a rotating file on Windows). Transcripts are *not* logged unless `privacy.log_transcripts`.
- **Single instance**: QLockFile (auto-released on crash) + per-user local server; second launch asks
  the running one to open settings.
- **Autostart**: `HKCU\...\Run` value `Assistant` = `"<venv>\Scripts\pythonw.exe" -I -m assistant --autostart`
  (`-I` = isolated: login cwd/env can't inject modules). Default **off** until onboarding asks (M5,
  default answer "yes"). If you disable it in Task Manager we respect that; toggling it on in our menu
  clears Windows' flag.
- **Threads, not asyncio**, in the core; Qt only in `ui/`. Worker threads publish events; the UI
  marshals them to the GUI thread with signals.
- Defaults: name "Jarvis", English, push-to-talk `Ctrl+Alt+Space`, panic `Ctrl+Alt+Esc`,
  conversations kept 90 days, LLM server port 8771.
- "Settings…" opens `config.toml` in your editor until the real settings window (M6); edits are
  picked up live (file watcher).

## How to test

### Automated (any OS)

```
uv sync
uv run pytest
```

(On Linux, Qt needs `libegl1 libgl1 libxkbcommon0 libfontconfig1 libdbus-1-3`; tests use `QT_QPA_PLATFORM=offscreen`.)

### Milestone 1 on Windows

1. Unzip the source, open PowerShell in the folder:
   `powershell -ExecutionPolicy Bypass -File scripts\dev-setup.ps1`
2. `scripts\run-cli.bat` → type `:status` → should show your i9-13900HX, RAM, and
   *RTX 4070 Laptop GPU, ~8188 MB VRAM*. Try `:config voice`, `:set voice.silence_timeout_s 1.2`,
   `:set general.language fr` (rejected), `:logs`, `:quit`.
3. `scripts\run-tray.bat` → a purple orb appears in the tray (maybe under the ^ overflow).
   Hover: "Jarvis — ready". Right-click menu:
   - *Pause listening* → icon turns grey with pause bars, tooltip "paused".
   - *Game mode* → orange badge on the icon.
   - *Start at login* → check with `reg query HKCU\Software\Microsoft\Windows\CurrentVersion\Run /v Assistant`;
     sign out/in → the icon comes back. Uncheck → value removed.
   - *Settings…* → `config.toml` opens; change `accent = "#22C55E"`, save → icon turns green within ~1 s.
     Put an invalid value (e.g. `language = "fr"`) → a "Settings problem" notification.
   - *Open folder* → Settings & data / Logs / Models open in Explorer.
   - *Quit* → icon disappears.
4. Run `scripts\run-tray.bat` twice → still only one icon; the second launch opens the config file.
5. `scripts\run-debug.bat` → same app with live log in the console (Ctrl+C quits).
6. Logs: `%LOCALAPPDATA%\Assistant\logs\` (`crash.log` should be empty).

## Known gaps / next steps

- Nothing has run on real Windows yet (this was built in a Linux container): first Windows test of
  M1 pending. Windows-only code paths in M1: winreg autostart, AUMID, DPI awareness, os.startfile.
- Next: M2 tools + permission gate + action log.
