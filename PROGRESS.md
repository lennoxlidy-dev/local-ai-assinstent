# PROGRESS

Living status file. Read this first when continuing in a new session (together with `SPEC.md`).

## Milestones

| # | Milestone | Status |
|---|-----------|--------|
| 1 | Skeleton: config in %APPDATA%, logging, tray, autostart, text-mode debug CLI | ✅ done (tested on Linux; Windows test pending by user) |
| 2 | Tools + permission gate + action log, unit tests with mocked destructive actions | ✅ done (146 tests; Windows backends type-checked, not yet run on Windows) |
| 3 | LLM server management (llama.cpp), tool-calling agent loop, fast-path intents | ✅ done (tested with real llama.cpp + Qwen3.5/Gemma 4 on CPU; GPU latency pending) |
| 4 | Voice: mic → wake word → VAD → STT → popup, then TTS; WAV test harness | ✅ done (real speech models tested end to end via WAV harness on CPU; mic/popup/hotkeys on Windows pending) |
| 5 | Onboarding wizard + profile/memory | ⏳ next |
| 6 | Settings/permissions GUI + history | — |
| 7 | Performance tuning, game mode, packaging (install.ps1/.bat/.exe), README | — |

## Architecture (as built so far)

```
src/assistant/
  __main__.py          entry point: tray (default) | --cli | --setup [--models ids] | --debug | --smoke-test N | --home DIR
                       | --say TEXT --out WAV [--lang --voice] | --voice-test WAV... [--speak-out WAV] [--fast]
  cli.py               text-mode debug CLI (":" commands; free text -> the assistant)
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
    assistant.py       the brain: text -> fast path | LLM agent; context line, history, conversations, stop/cancel
  win/shell.py         AppUserModelID, per-monitor DPI awareness (CLI), open_path()
  ui/
    app.py             QApplication bootstrap, theme, config file watcher, single instance
    tray.py            tray icon + menu (settings, pause, game mode, start at login, folders, quit)
    icons.py           orb icons drawn at runtime (accent color, paused bars, game-mode badge)
    single_instance.py QLockFile + per-user QLocalServer (2nd launch -> "show" to the 1st)
    confirm.py         permission questions: popup (click or spoken yes/no, first answer wins); dialog fallback
    popup.py           the popup: PopupModel (QML bridge) + PopupController (events -> orb state, fade, mini chat)
    qml/Popup.qml      compact card (orb, live transcript, answer, permission question) / mini chat
    qml/Orb.qml        animated orb: rings with the mic level, spin while thinking, pulse while speaking
  permissions/
    policy.py          capabilities (allow/ask/block), risks, folder rules, permissions.toml
    pathrules.py       Windows path canonicalization (ntpath; junction/8.3/ADS/device-path proof) + rule matching
    gate.py            PermissionGate.evaluate/authorize, Confirmer protocol, Task (cancel token + grants)
  storage/             SQLite (WAL): action_log, facts, routines, timers, conversations (+FTS5 search)
  llm/
    models.py          model catalog (HF repo, file, size, SHA-256, sampling, thinking switch), choose_model()
    download.py        resumable + SHA-256-verified downloads (stdlib)
    setup.py           llama.cpp Windows release picker/installer (CUDA 13.5 if driver>=580, else 12.4; Vulkan/CPU)
    server.py          llama-server process: hidden, API key via env, job object, health wait, crash restart
    client.py          streaming OpenAI-compatible client (http.client): content/reasoning/tool-call deltas, cancel
    prompts.py         EN/DE system prompt, live context line, untrusted-data markers, thinking heuristic
    agent.py           multi-step tool loop, step limit, loop detection, FAILED marking, think-tag filter
    service.py         LlmService: model selection, start/stop/warm-up, chat() with per-model sampling, vision
    vision.py          look_at_screen tool (vision model; last resort after UIA and OCR)
  intents/             fast path: numbers.py (EN/DE number words), fastpath.py (anchored EN/DE patterns)
  testing/fakes.py     fake OS backends (shared by tests and the benchmark)
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
  audio/
    sources.py         MicSource (sounddevice, 16 kHz or native + soxr), ArraySource (WAV harness), RingBuffer
    player.py          streaming output (stoppable instantly), NullPlayer for tests
    earcons.py         wake / end / error / alarm sounds (synthesized, no files)
  voice/
    models.py          voice model catalog (URL, size, SHA-256) + install check/download
    wakeword.py        any-name keyword spotting (BPE tokens at runtime), sensitivity, calibration
    endpoint.py        Silero VAD endpointing (silence timeout, wait-for-speech, max length)
    stt.py             Parakeet TDT v3 transcriber (lazy load at wake, idle unload)
    tts.py             Supertonic 3 speaker: sentence queue, streaming from LLM deltas, idle unload
    voicelock.py       speaker verification (CAM++ voiceprint, cosine score)
    pipeline.py        the state machine: wake -> listen -> transcribe -> answer -> speak -> follow-up;
                       barge-in, cancel, push-to-talk, spoken confirmations, mic reopen
    yesno.py           spoken yes/no/always/stop (EN+DE), conservative
    calibration.py     record from the mic; wake-word calibration helper
    runtime.py         build_voice() from config (+ live config changes); harness.py: --say / --voice-test
  win/
    hotkeys.py         RegisterHotKey thread: push-to-talk (press/hold), panic
    dwm.py             popup styling: acrylic blur, rounded clip, WS_EX_NOACTIVATE
    qos.py             EcoQoS for the always-on audio thread
    backends.py        Windows implementations (pywin32, pyvda, pycaw, uiautomation, WinRT SMTC/OCR, mss,
                       send2trash IFileOperation, Everything SDK, SHGetKnownFolderPath)
    inputs.py          SendInput (fixed-width ctypes structs, unit-tested layout)
    com.py             per-thread COM init; WinRT worker thread (MTA + asyncio)
tests/                 pytest (292 tests; test_llm_integration.py runs a real llama-server, test_voice_pipeline.py
                       the real speech models - each skipped when not installed)
bench/                 llm_bench.py + cases.py: GPU latency + tool-calling accuracy per model
scripts/               dev-setup.ps1, setup-models.bat, run-tray.bat, run-debug.bat, run-cli.bat
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

Milestone 3 decisions:

- **llama.cpp, not Ollama**: Ollama can't load these Qwen3.5 GGUFs with their separate vision files, and we need
  llama-server features directly: per-request thinking switch, prompt cache, `--sleep-idle-seconds`, MTP
  speculative decoding, `--fit`. The app manages the process itself (hidden, random API key passed via env so
  browser pages can't use the model, Windows job object so it dies with us, crash restart with backoff).
- **Models (verified on Hugging Face, Nov 2026, all Apache-2.0)**: default smart = **Qwen3.5 9B Q4_K_M**
  (5.3 GB + 0.9 GB vision), fast = **Qwen3.5 4B Q4_K_M** (2.6 GB + 0.6 GB). Alternatives in the catalog:
  Gemma 4 E4B, Granite 4.2 8B (text only), Ministral 3 8B, and Qwen3.5 9B **MTP** (multi-token prediction,
  `--spec-type draft-mtp`, experimental: faster decoding). Qwen3.6 exists only at 27B/35B - too big for 8 GB.
  `profile = "auto"` uses the benchmark's recommendation, else picks by free VRAM.
- **Thinking**: Qwen3.5 small models have thinking *off by default*; we enable it per request
  (`chat_template_kwargs.enable_thinking`) only when a heuristic sees a complex/multi-step request
  (`llm.thinking = auto|on|off`).
- **Prompt cache layout**: Qwen3.5's template puts the ~5k-token tool block *before* the system text, then
  history, then the new user turn. Time/active window/desktop go into a one-line context prefix on the user
  turn, so a request only processes its own ~50-150 tokens (measured: `cache_n=5778, prompt_n=64`).
  A new remembered fact only re-processes the system text (~700 tokens), never the tool block. The server is
  warmed up once at start. Default context: 16k tokens.
- **Prompt injection, enforced in code**: tool output from files/clipboard/screen/commands is wrapped in
  per-task random markers (`<<DATA-xxxxxx …>>`; look-alike markers inside the data are neutralized) and the
  task becomes *tainted*: from then on every side-effecting capability (write/move/delete files, terminal,
  input, opening apps/URLs, memory, routines, clipboard writes, power, installs, admin) asks for confirmation
  even if set to "allow", and "for this task"/"always" answers are not offered.
- **Failures can't be misread**: failed/declined/blocked tool results reach the model as
  `FAILED (nothing was done): …` (a 4B model once claimed "deleted anyway" before this).
- **Fast path**: anchored full-sentence EN/DE patterns (volume, media, play X, open/close app, timers,
  reminders, time/date, lock, desktops, brightness, stop) + number words ("fünfundzwanzig", "forty-five",
  "halbe Stunde"); slots are validated (the app must exist), everything else goes to the LLM. Replies are
  localized. "play music" with no player running falls back to opening your music app and pressing play.
  Measured 1-3 ms (+ tool time). Windows app names in both languages are aliases (Editor = Notepad, ...).
- **Conversations**: same conversation continues for `llm.conversation_timeout_min` (5) minutes; history is
  trimmed by whole turns to ~16k chars; saved to SQLite with FTS5 search unless `privacy.save_conversations`
  is off; retention purge at start.
- **Idle/VRAM**: `llm.idle_unload_min` -> llama-server sleeps (frees the model) and reloads on the next
  request. The tray's *Game mode* toggle stops the server immediately (VRAM free) and reloads afterwards.
  Vision projector is moved to the CPU automatically when VRAM is tight.

Milestone 4 decisions:

- **One speech runtime: sherpa-onnx 2.0** (ONNX Runtime inside; CPU; no PyTorch) for wake word, VAD, speech
  recognition, speech output and speaker verification. Windows wheels for Python 3.14 exist. The GPU stays free
  for the language model and games.
- **Wake word = any name, no training**: an open-vocabulary keyword spotter (zipformer, GigaSpeech, 3.3M params,
  ~1.5 % of one core here). The name is turned into model tokens at runtime (umlauts -> ae/oe/ue); the
  sensitivity slider maps to threshold/boost. **Double-check**: the final transcript must start with the name
  (fuzzy match >= 85 incl. learned variants), so near-misses ("Travis", a TV) are dropped silently.
  **Calibration** (`:calibrate` now, onboarding in M5): you say the name 3x; it learns how you say it (e.g.
  "Javis") and picks the lowest sensitivity that still catches all takes. Limitation: the spotter is
  English-trained - a German pronunciation of an English name is weaker (a German-accented "Jarvis" was missed
  0/8 times uncalibrated here; English pronunciation 12/12, and no false triggers on the test speech without the
  name). Names with 2-3 syllables work best.
- **Endpointing**: Silero VAD v5, request ends after `voice.silence_timeout_s` (1.0 s) of silence, max 20 s.
  1.6 s of audio from before the trigger is kept (pre-roll), so the double-check hears the name and nothing
  you say right after it is lost. "Jarvis ... (pause) ... open notepad" works as a two-step request.
- **Speech recognition: Parakeet TDT 0.6B v3 (int8)**: EN + DE (25 languages), punctuation and casing, fast on
  CPU (310 ms for a sentence here). Whisper was the alternative but is much slower on CPU and the GPU is reserved
  for the LLM. It needs ~870 MB RAM, so it loads when the wake word fires (in parallel with your speech), stays
  10 min (`voice.stt_keep_loaded_min`) and is then unloaded - idle RAM stays ~200 MB. Live transcript every 0.8 s.
- **Speech output: Supertonic 3 (int8) instead of Kokoro (EN) + Piper (DE)** - a deliberate deviation from the
  recommended stack: sherpa-onnx's Windows wheels no longer contain the espeak-ng phonemizer, so Piper refuses
  to load ("no frontend available") and Kokoro silently drops every word it can't phonemize. Supertonic needs no
  phonemizer, speaks EN and DE with one 130 MB model, has 10 voices (`voice.tts_voice_en/_de` = F1-F5, M1-M5),
  was 4-5x faster than Kokoro here (real-time factor ~0.17 vs ~0.8) and had the lowest word error when its
  output was transcribed back. Answers are spoken sentence by sentence while the LLM is still writing.
- **Interrupting**: the wake word stays active while it thinks/speaks; saying the name stops the speech at once
  (barge-in) and starts a new request, so "Jarvis, stop" works mid-sentence. The panic hotkey (Ctrl+Alt+Esc)
  silences it, cancels the request and releases held keys/mouse buttons. A generation counter guarantees that a
  cancelled request can't speak or change state later.
- **Follow-up**: after an answer it listens 5 s (`voice.follow_up_s`) without the name (at least 2 words).
- **Spoken confirmations**: for a voice request, permission questions appear in the popup (exact command/paths +
  buttons) *and* are asked aloud ("Delete report.docx? Say yes or no."). A clear yes / ja / okay / go ahead,
  "always", "for this task", or no / nein / stop answers it; whatever comes first (voice or click) wins; unclear
  -> asked once more, then only the click remains (90 s -> no). Anything negative or ambiguous is never a yes.
  The voice lock (if on) also applies to these answers.
- **Popup**: Qt Quick/QML with the *software* renderer (no GPU use, reliable per-pixel transparency for a window
  this small). It is created hidden at startup and appears 2-3 ms after the wake-word event. A non-activating
  tool window (Qt.Tool + WindowDoesNotAcceptFocus + WS_EX_NOACTIVATE): it never takes the focus - until you click
  it to open the mini chat, which then accepts typing (Esc/✕ closes). Acrylic blur via
  SetWindowCompositionAttribute (Windows 10 1803+/11, `appearance.popup_blur`) with a rounded clip; theme,
  accent and `appearance.popup_position` (on the monitor with the mouse) are configurable; it fades
  `appearance.popup_timeout_s` after the answer (hovering keeps it open). Timers/reminders: popup + alarm sound
  + the label spoken. The tray's "Type a request…" opens the mini chat.
- **Voice lock (optional)**: 3D-Speaker CAM++ voiceprint (only a 192-number vector is stored, no audio); cosine
  score >= `voice.voice_lock_threshold` (0.55). In the tests an enrolled voice scored >0.2 higher than a different
  voice saying the same sentence. `:enroll` / `:enroll forget` in the CLI (GUI in M5/M6).
- **Audio**: sounddevice/PortAudio (Windows default device or `voice.input_device` by name); opens at 16 kHz or
  natively with soxr resampling. Audio stays in RAM (4 s ring buffer + the current request) and is never written
  to disk. A lost microphone (unplugged headset) is reopened automatically. Output closes the device after 3 s
  idle. Pause in the tray really closes the microphone.
- **Hotkeys**: RegisterHotKey (no keyboard hook watching your typing). Push-to-talk Ctrl+Alt+Space (press mode:
  talk, it ends at the pause or on a second press; `voice.push_to_talk_mode = "hold"`: talks while held);
  panic Ctrl+Alt+Esc. Without voice models the push-to-talk key opens the chat box.
- **Efficiency**: the always-on audio thread is an EcoQoS (efficiency) thread on Windows 11; heavy work runs in
  worker threads; speech recognition and speech output are unloaded after 10 idle minutes.

Voice measurements (this Linux container: 4 vCPU Xeon, CPU only - please re-check on the i9):

| What | Measured | Spec target |
|------|----------|-------------|
| Idle CPU, tray app with wake word running | 3.8 % of one core (≈1 % of this 4-core machine as Task Manager counts it) | < 2 % |
| Idle RAM (RSS) | 205 MB | < 300 MB |
| Wake-word event -> popup visible | 2-3 ms (window pre-created) | < 300 ms |
| End of speech -> transcript (after the 1 s silence that marks the end) | 310 ms | < 500 ms |
| ...first request after speech recognition was unloaded | ~2.6 s (model loads while you speak; ~4.5 s load here) | |
| Fast-path command -> reply | 2-3 ms | < 1 s |
| Reply -> first spoken audio | ~550 ms (synthesis of the first sentence) | |
| RAM while speech models are loaded | ~1.1 GB (back to ~200 MB after 10 idle minutes) | |

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

### Milestone 3 on Windows

1. Download llama.cpp + models (once, ~10 GB; resumable, checksums verified):
   `scripts\setup-models.bat`  (or a single model: `.venv\Scripts\python -m assistant --setup --models qwen3.5-4b`)
2. `scripts\run-cli.bat`, then just type:
   - fast path (instant): `set the volume to 30`, `Lautstärke auf 40`, `nächstes Lied`, `wie spät ist es`,
     `timer for 5 minutes`, `open notepad`, `schließe den Editor`, `switch to desktop 2`, `stop`
   - LLM (first request after start loads the model, ~5-15 s): `What's my IP address?`,
     `Find my newest PDF in Downloads and tell me its name`, `Remember that my sister's name is Lea`,
     `Erstelle auf dem Desktop eine Datei einkauf.txt mit Milch, Eier, Brot` (asks first),
     `Summarize the file Documents\<something>.docx`, `What is on my screen right now?` (vision)
   - `:fast <sentence>` shows what the fast path would do, `:llm status`, `:llm models`, `:llm use qwen3.5-4b`,
     `:history`, `:history <words>`, `:new`, `:log 20`
   - each answer shows how it was handled and the time, e.g. `[fast:volume.set in 4 ms]`,
     `[llm in 1840 ms, first token 420 ms]`
3. Tray: right-click -> **Type a request…** (answers appear as notifications; permission questions as a dialog).
   Hover the icon: "(model loading…)" while it starts. *Game mode* unloads the model (check Task Manager -> GPU).
4. Benchmark on your GPU (≈5-10 min per model):
   `uv run python bench/llm_bench.py` (smart + fast) or
   `uv run python bench/llm_bench.py --models qwen3.5-9b,qwen3.5-4b,gemma-4-e4b,qwen3.5-9b-mtp`
   It prints accuracy, "reply starts" p50/p90 and load time per model and saves the recommendation that
   `llm.profile = "auto"` uses. Please send me the table.

### Milestone 4 on Windows (voice)

1. `powershell -ExecutionPolicy Bypass -File scripts\dev-setup.ps1` (installs the new packages: sherpa-onnx,
   sounddevice, soxr, numpy, sentencepiece), then `scripts\setup-models.bat` again - it now also downloads the
   speech models (~0.7 GB; resumable, SHA-256 checked). Then `uv run pytest` - `test_voice_pipeline.py` now runs
   against the installed models (synthesized speech through the real pipeline, ~1-2 min).
2. Without a microphone - synthesize test audio and run it through the real pipeline:
   ```
   .venv\Scripts\python -m assistant --say "Jarvis, what time is it?" --out t1.wav
   .venv\Scripts\python -m assistant --say "Jarvis, stell einen Timer auf zwei Minuten." --lang de --voice M2 --out t2.wav
   .venv\Scripts\python -m assistant --voice-test t1.wav t2.wav --speak-out replies.wav --debug
   ```
   It prints every pipeline event with timestamps plus TIMING lines; `replies.wav` holds what it said.
3. With your microphone in the CLI (`scripts\run-cli.bat`):
   - `:voice` (models, devices), `:voice devices`, `:set voice.input_device "Headset"` if the default is wrong
   - `:calibrate` - say the name 3x (tunes the sensitivity, learns your pronunciation)
   - `:voice on`, then: "Jarvis, what time is it?" / "Jarvis ... (pause) ... open notepad" / right after an answer
     "and what's the date?" (follow-up, no name) / "Jarvis, delete test.txt on my desktop" (asks aloud - answer
     "yes" or "no") / `:voice off`
   - `:voice say Hallo, ich bin Jarvis.` (`:set voice.tts_voice_de M3` to try other voices)
   - `:enroll` (read 3 sentences) -> voice lock on; let a video say "Jarvis, ..." -> rejected. `:enroll forget`.
4. Tray app (`scripts\run-debug.bat` shows the log):
   - tooltip "listening for “Jarvis”". "Jarvis, set a timer for one minute" -> popup at the top center: the orb
     reacts to your voice, live transcript, then answer + speech. Keep typing in another window meanwhile - the
     popup must never take the focus. After 1 min: alarm sound + popup + spoken label.
   - Ctrl+Alt+Space = push-to-talk (no name needed); Ctrl+Alt+Esc while it talks = silence + cancel;
     "Jarvis, stop" while it answers.
   - Click the popup -> mini chat (type "open notepad"; Esc closes). Tray -> "Type a request…" opens it too.
   - A risky request: the popup shows the exact path/command with buttons and it asks aloud - click or say yes/no.
   - Tray -> Pause listening: the Windows microphone indicator disappears; resume brings it back.
   - `config.toml`: `appearance.popup_position = "bottom-right"`, `theme = "light"`, `accent = "#22C55E"`,
     `popup_blur = false` (if the blur looks wrong), `voice.push_to_talk_mode = "hold"`.
   - Task Manager: idle ~0 % CPU and ~200 MB; ~1.1 GB right after a voice request, back down after 10 minutes.
5. Please report: wake-word hit rate with your pronunciation (before/after `:calibrate`), false triggers while a
   video/podcast plays, how the popup looks (blur, corners, position), whether focus was ever stolen, and the
   TIMING lines from `%LOCALAPPDATA%\Assistant\logs\assistant-debug.log`.

## Benchmark results (tool-calling accuracy, measured here on CPU - latency must be re-measured on the GPU)

24 realistic EN/DE cases on a simulated PC (real agent loop, tool registry and permission gate; fake OS
backends; confirmations auto-approved), checked by the resulting state. Run on the Milestone-3 code
(fast path disabled so the LLM handles every case):

| Model | Passed | Failures | Prompt injection | Notes |
|-------|--------|----------|------------------|-------|
| Qwen3.5 4B Q4_K_M | **24/24** | - | resisted | 0 invalid tool arguments |
| Qwen3.5 9B Q4_K_M | 22/24 | remember_de (claimed "ich merke mir…" without calling the tool), window_list_close (read "Rechner" as *computer* and called **shutdown** - would have stopped at the mandatory confirmation) | resisted | |
| Gemma 4 E4B Q4_K_M | 21/24 | find_invoice (asked for details instead of searching), remember_de (no tool call), window_list_close (same "Rechner" -> shutdown mistake) | resisted | ~2-3x faster than Qwen on CPU; 0 invalid arguments |

Targeted re-check after the follow-ups below (6 hardest cases): **9B 5/6** (now closes the Calculator
instead of shutting down; still skips the `remember` call in German - covered by the fast path, and if the
model still claims an action after the nudge the user now hears "(Note: I didn't actually run any action…)"),
**Gemma 4 E4B 3/6** (still shuts down on "Rechner" - would stop at the confirmation - and still doesn't search
for the invoice), **4B 6/6**.

Conclusion: Qwen3.5 stays the model family (Gemma 4 E4B is faster but follows instructions less reliably).
4B was the most dependable on these concrete tool tasks; 9B is kept as the "smart" default per the spec rule
(the benchmark picks it if its reply starts within 2 s on your GPU and it isn't clearly less accurate).

Follow-ups already in the code: a prompt + tool-description rule that power actions need an explicit request ("Rechner" usually means the Calculator app), the claim-without-tool guard (agent nudges the model once when it claims an
action that no tool performed) and a fast-path "remember/merk dir" intent. Latency on the RTX 4070 decides
9B vs 4B (spec rule: 9B if "reply starts" <= 2 s); run `bench/llm_bench.py` there.

## Known gaps / next steps

- Nothing has run on real Windows yet (built in a Linux container). M1/M2 Windows code is type-checked
  against the real Windows wheels (pyright, platform=Windows: 0 errors) and the SendInput struct layout is
  unit-tested, but **first real-Windows run is pending** - expect a round of fixes, especially for
  Spotify UI automation, focus stealing rules, pyvda on your exact build, and WMI brightness.
- TODO later: Spotify Web API (optional own credentials), Windows Search as a middle search tier,
  vision-based clicking (M3).
- M3 open points: GPU latency targets (reply starts < 2 s, fast path < 1 s) must be measured on your laptop with
  the benchmark.
- M4 open points: microphone/speaker (WASAPI), hotkeys, the acrylic/rounded non-activating popup and EcoQoS are
  written against the Windows APIs and type-checked but have not run on Windows yet. The wake word is weaker for
  German pronunciations of English names (calibration helps; there is no German keyword-spotting model in
  sherpa-onnx). Tool summaries in permission questions are English even with a German UI. No acoustic echo
  cancellation: with loud speakers its own voice can reach the mic (mitigated: barge-in needs the name, and the
  name inside its own speech is ignored) - headphones avoid it. Calibration/enrollment are CLI commands until the
  onboarding wizard (M5) and settings window (M6).
- Next: M5 - onboarding wizard (>= 15 questions, say the name 3x to calibrate) + profile/memory.
