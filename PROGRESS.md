# PROGRESS

Living status file. Read this first when continuing in a new session (together with `SPEC.md`).

## Milestones

| # | Milestone | Status |
|---|-----------|--------|
| 1 | Skeleton: config in %APPDATA%, logging, tray, autostart, text-mode debug CLI | ✅ done (tested on Linux; Windows test pending by user) |
| 2 | Tools + permission gate + action log, unit tests with mocked destructive actions | ✅ done (146 tests; Windows backends type-checked, not yet run on Windows) |
| 3 | LLM server management (llama.cpp), tool-calling agent loop, fast-path intents | ⏳ next |
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
  permissions/
    policy.py          capabilities (allow/ask/block), risks, folder rules, permissions.toml
    pathrules.py       Windows path canonicalization (ntpath; junction/8.3/ADS/device-path proof) + rule matching
    gate.py            PermissionGate.evaluate/authorize, Confirmer protocol, Task (cancel token + grants)
  storage/             SQLite (WAL): action_log, facts, routines, timers (+ conversations in M3)
  tools/
    base.py            Tool/Plan/ToolResult, ToolRegistry.invoke = validate -> plan -> gate -> log -> run -> log
    schema.py          tiny JSON-schema DSL (strict schema for the LLM, validation before every call)
    platform.py        backend interfaces + Unsupported fallback; create_platform()
    runtime.py         build_tool_system(): wires platform, gate, log, services, registry
    files.py extract.py file_index.py   find/list/read/create/copy/move/rename/delete(Recycle Bin)/open
    apps.py            app index matching, open_app (+desktop rules, DesktopGuard), window, desktop, open_url
    media.py           media (SMTC), play_music (Spotify search URI + UI Automation "play" button)
    system.py timers.py   volume, brightness, clipboard, power, timers/reminders
    screen.py          mouse, keyboard, ui_elements, click_element (UIA -> OCR), read_screen, screenshot
    terminal.py        run_command (+read-only classifier), run_as_admin (UAC), winget
    memory.py routines.py   remember/recall/forget; create/run/list/delete routines
  win/
    backends.py        Windows implementations (pywin32, pyvda, pycaw, uiautomation, WinRT SMTC/OCR, mss,
                       send2trash IFileOperation, Everything SDK, SHGetKnownFolderPath)
    inputs.py          SendInput (fixed-width ctypes structs, unit-tested layout)
    com.py             per-thread COM init; WinRT worker thread (MTA + asyncio)
tests/                 pytest (146 tests) + fakes.py (fake OS backends)
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

Milestone 2 decisions:

- **One code path for every tool call** (`ToolRegistry.invoke`): validate args -> plan (exact paths,
  command, risks) -> permission gate -> action-log row -> run -> outcome. Blocked/denied/invalid calls are
  logged too. Routines are just sequences of normal tool calls, so every step is checked.
- **Permission model**: capability level (allow/ask/block) x folder rule (full/read/ask/none, most
  specific wins) x risk tags. Risky actions (delete, move, overwrite, terminal changes, installs, closing
  apps with unsaved work, force-close, admin, power) ask *even if the capability is "allow"* unless you
  remove that risk from `always_confirm` (admin can never be removed). Answers: once / for this task
  (never offered for risky actions) / always (persists "allow"; only for non-risky asks).
- **Defaults**: reads allowed everywhere; Windows, Program Files, ProgramData read-only; SSH/GPG keys,
  Windows credential stores and browser profiles (passwords/cookies) **blocked** even for reading
  (and hidden from search results); the assistant's own config/data folders can never be written by a
  tool (so a prompt-injected instruction can't grant itself permissions). Mouse/keyboard = ask (grant per task).
- **Hard refusals no setting overrides**: deleting/moving a drive root, your profile folder or a main
  folder (Desktop/Documents/Downloads...); killing Windows system processes; non-http URLs in open_url.
- **Delete = Recycle Bin only**: send2trash's IFileOperation path with a progress sink that aborts any
  non-recyclable delete; the legacy SHFileOperation fallback (which can delete permanently) is refused.
  Overwrites move the old file to the Recycle Bin first.
- **Terminal classifier is an allowlist**: only known information commands (Get-ChildItem, ipconfig /all,
  systeminfo, ...) with no redirection/sub-expressions/script blocks/.NET calls/escapes/variables run
  without asking; paths they mention are checked against folder rules; network commands always ask.
  PowerShell runs via `-EncodedCommand` (no quoting bugs), output is untrusted data.
- **Untrusted data**: tool results from files, clipboard, screen/OCR, UI element names and command output
  are flagged `untrusted`; the agent (M3) wraps them as data, never instructions.
- **Apps**: index from `Get-StartApps` (Start Menu + Store apps, cached 24 h) with Start Menu .lnk fallback;
  launch via `shell:AppsFolder\<AppID>`; fuzzy matching (rapidfuzz) + aliases (`music` -> your music app).
- **Virtual desktops**: pyvda 0.6 (supports up to Win11 24H2/25H2 builds); "Assistant" desktop is created
  and named on first use; default rule `spotify = "assistant"`. `DesktopGuard` restores your desktop and
  focus after apps that yank you to their desktop. If pyvda fails, apps open on the current desktop.
- **Spotify "play X"**: `spotify:search:X` + UI Automation finds the top result's "Play …" button
  (EN/DE names) and invokes it without moving the mouse. Spotify Web API (optional credentials) is still TODO.
- **File search**: Everything SDK DLL if present (`%LOCALAPPDATA%\Assistant\bin\Everything64.dll`;
  the M7 installer will fetch it), else our own trigram-FTS5 filename index built by a
  background-priority thread (low CPU *and* I/O priority), else a bounded folder scan.
- **COM/WinRT threading**: classic COM per thread (STA); WinRT (SMTC, OCR) on one MTA worker thread.

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

### Milestone 2 on Windows (debug CLI)

`scripts\run-cli.bat`, then try (each answer prompt: y = once, t = for this task, a = always, n = no):

```
:tools                                              list all tools  (:tools open_app shows one schema)
:tool open_app {"name": "notepad"}                  opens Notepad (no question: apps.open = allow)
:tool keyboard {"action": "type", "text": "Hallo Welt", "window": "notepad"}   asks (input), then types
:tool window {"action": "close", "target": "notepad"}   Notepad asks to save (WM_CLOSE, nothing forced)
:tool open_app {"name": "spotify"}                  Spotify opens on a new "Assistant" desktop; you stay put
:tool play_music {"query": "Bohemian Rhapsody"}     should start the top result (report what happens!)
:tool media {"action": "status"}                    / pause / next
:tool volume {"level": 30}                          :tool brightness {"level": 60}
:tool desktop {"action": "list"}                    / {"action": "switch", "target": "assistant"} / "1"
:tool find_files {"query": "invoice"}               (uses the fallback index/folder scan unless Everything SDK is present)
:tool read_file {"path": "Documents\\some.pdf"}
:tool create_file {"path": "Desktop\\test.txt", "content": "hello"}      asks
:tool rename_path {"path": "Desktop\\test.txt", "new_name": "test2.txt"}  asks (move risk)
:tool delete_path {"path": "Desktop\\test2.txt"}  asks; then check the Recycle Bin
:tool read_file {"path": "~\\.ssh\\id_rsa"}        Blocked (credential folder)
:tool create_file {"path": "C:\\Windows\\x.txt", "content": "x"}   Blocked (protected folder)
:tool run_command {"command": "Get-Date"}           runs directly (read-only)
:tool run_command {"command": "New-Item $env:TEMP\\x.txt"}   asks, shows the exact command
:tool run_as_admin {"command": "whoami /groups"}    asks, then the UAC prompt
:tool timer {"action": "set_timer", "seconds": 10, "label": "tea"}   prints a notification after 10 s
:tool ui_elements {"window": "notepad"}             buttons/menus with coordinates
:tool read_screen {}                                OCR of the whole screen
:tool remember {"fact": "My sister's birthday is May 3rd"}    then :facts
:tool create_routine {"name": "test", "steps": [{"tool": "volume", "args": {"level": 25}}, {"tool": "open_app", "args": {"name": "calculator"}}]}
:tool run_routine {"name": "test"}
:log 20                                             the action log (incl. denied/blocked)
:perm                                               permission table; :perm input allow   to change
```

Permissions file: `%APPDATA%\Assistant\permissions.toml` (editable; invalid entries fall back to safe defaults).

## Known gaps / next steps

- Nothing has run on real Windows yet (built in a Linux container). M1/M2 Windows code is type-checked
  against the real Windows wheels (pyright, platform=Windows: 0 errors) and the SendInput struct layout is
  unit-tested, but **first real-Windows run is pending** - expect a round of fixes, especially for
  Spotify UI automation, focus stealing rules, pyvda on your exact build, and WMI brightness.
- TODO later: Spotify Web API (optional own credentials), Windows Search as a middle search tier,
  vision-based clicking (M3), panic hotkey wiring (M4), timers/notifications in the tray app (M3/M4).
- Next: M3 - llama.cpp server management, agent loop with tool calling, fast-path intents.
