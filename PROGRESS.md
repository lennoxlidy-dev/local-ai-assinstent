# PROGRESS

Living status file. Read this first when continuing in a new session (together with `SPEC.md`).

## Milestones

| # | Milestone | Status |
|---|-----------|--------|
| 1 | Skeleton: config in %APPDATA%, logging, tray, autostart, text-mode debug CLI | ✅ done (tested on Linux; Windows test pending by user) |
| 2 | Tools + permission gate + action log, unit tests with mocked destructive actions | ✅ done (146 tests; Windows backends type-checked, not yet run on Windows) |
| 3 | LLM server management (llama.cpp), tool-calling agent loop, fast-path intents | ✅ done (tested with real llama.cpp + Qwen3.5/Gemma 4 on CPU; GPU latency pending) |
| 4 | Voice: mic → wake word → VAD → STT → popup, then TTS; WAV test harness | ✅ done (real speech models tested end to end via WAV harness on CPU; mic/popup/hotkeys on Windows pending) |
| 5 | Onboarding wizard + profile/memory | ✅ done (wizard logic, voice answers and calibration tested with real models; GUI rendered offscreen - Windows run pending) |
| 6 | Settings/permissions GUI + history | ✅ done (all 10 tabs loaded and exercised offscreen; Windows run pending) |
| 7 | Performance tuning, game mode, packaging (install.ps1/.bat/.exe), README | ✅ done (benchmark run here on CPU; .exe built + self-tested on Linux; installer scripts, game-mode probe and GPU latency need a Windows run) |

## Architecture

```
src/assistant/
  __main__.py          entry point: tray (default) | --cli | --setup [--models auto|ids] | --onboarding | --debug
                       | --smoke-test N | --self-test | --quit | --home DIR
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
    gamemode.py        GameModeWatcher: fullscreen-game detection (classify) with hysteresis + manual override
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
    onboarding.py      setup wizard window: SetupModel (QML bridge) + SetupWindow (voice events, downloads, pages)
    qml/Setup.qml      wizard shell (header, progress, page loader, downloads, buttons); qml/setup/*.qml pages
    settings.py        settings window: SettingsModel (``settings.call(action, args)``) + SettingsWindow (do_* actions)
    settings_text.py   settings texts EN/DE
    qml/Settings.qml   sidebar + lazily loaded tab; qml/settings/*Tab.qml (10 tabs)
    qml/common/*.qml   shared controls: Btn, Card, SettingRow, ToggleSwitch, InputField, Dropdown, ValueSlider,
                       KeyField, Segmented, OptionRow, DangerBtn, Toggle
    theme.py           Look: the live colour palette (``look.colors``) for the QML windows
    selftest.py        --self-test: compile every QML file, open wizard / settings (all tabs) / popup, report
    qtmodel.py         helper: QObject view-models with notifying properties for QML
  onboarding/
    questions.py       the 27 setup questions (EN/DE text, defaults, options, validation, apply -> config/policy/db)
    interpret.py       typed/spoken answers: names, choices (labels, synonyms, ordinals), yes/no, numbers, nav words
    wizard.py          state machine: one question at a time, back/skip, conditions, answers applied immediately
    voice.py           speaks questions, listens for answers, records the name 3x / voice-lock sentences, mic meter
    downloads.py       SetupManager: background downloads (speech models -> llama.cpp -> LLM), retry, disk check
    text.py            the same wizard as text (debug CLI :setup)
    env.py             what the wizard needs from the app (+ Windows display name / UI language guesses)
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
    recordings.py      optional WAV copies of voice requests (privacy.save_audio, off by default) + retention
    runtime.py         build_voice() from config (+ live config changes); harness.py: --say / --voice-test
  win/
    hotkeys.py         RegisterHotKey thread: push-to-talk (press/hold), panic
    dwm.py             popup styling: acrylic blur, rounded clip, WS_EX_NOACTIVATE
    qos.py             EcoQoS for the always-on audio thread
    fullscreen.py      foreground-window probe for game mode (monitor coverage, D3D exclusive, exe, 3D DLLs)
    backends.py        Windows implementations (pywin32, pyvda, pycaw, uiautomation, WinRT SMTC/OCR, mss,
                       send2trash IFileOperation, Everything SDK, SHGetKnownFolderPath)
    inputs.py          SendInput (fixed-width ctypes structs, unit-tested layout)
    com.py             per-thread COM init; WinRT worker thread (MTA + asyncio)
tests/                 pytest (419 tests; test_llm_integration.py runs a real llama-server, test_voice_pipeline.py
                       the real speech models - each skipped when not installed)
bench/                 llm_bench.py + cases.py: GPU latency + tool-calling accuracy per model;
                       perf_bench.py: every performance target of the spec (idle, wake, STT, TTS, fast path, LLM)
scripts/               dev-setup.ps1, setup-models.bat, run-tray.bat, run-debug.bat, run-cli.bat, perf-bench.bat,
                       build-exe.ps1/.bat
install.bat/.ps1       one-click per-user install/update; uninstall.ps1
packaging/             assistant.spec (PyInstaller), launch.py (exe entry), make_icon.py -> assistant.ico
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

Milestone 5 decisions:

- **27 questions** (spec: >= 15), one per page, in this order: language, assistant name, say the name 3x
  (wake-word calibration), what to call you, about you + where you live (optional), voice replies, voice (with
  ▶ preview), personality, answer length, microphone + level meter, music app, browser, everyday apps (mail,
  notes, code, chat, calls -> spoken aliases), folders, capability permissions (Allow/Ask/Block per capability),
  actions that always need confirmation, separate "Assistant" desktop, per-app desktop rules, autostart, hotkeys
  (push-to-talk, panic, press/hold), silence timeout, game mode, speed vs smart model, save conversations,
  retention, voice lock (read 3 sentences), routines (templates: gaming mode, work mode, music time, good night).
  Questions that don't apply are hidden (no voice question when voice replies are off, no retention when
  conversations aren't saved, no desktop rules without the separate desktop).
- **Every question has a default and can be skipped** - skipping *applies the shown suggestion* (e.g.
  autostart on, the detected music app), so what you saw is what you get. Defaults come from your PC where
  possible: Windows display language, your first name from the Windows account, installed music apps/browsers,
  GPU VRAM. Answers are written immediately (a language change switches the next question; nothing is lost if
  the window closes). Closing early asks "use the suggestions for the rest?". Re-run: tray menu
  **Setup wizard…** (or `--onboarding`); it then starts from your current settings. The settings window (M6)
  will edit every answer individually.
- **Speaking and listening**: each question is spoken (when the speech model is downloaded and voice replies
  are on - "Read questions aloud" toggle). With "Answer by voice" it then listens (VAD, up to 8 s) and
  interprets the answer: names ("call me Lennox" -> Lennox, "ich heiße Anna-Lena"), choices by label in either
  language, synonyms or position ("the second one", "die erste"), yes/no (same conservative parser as
  confirmations), numbers ("eineinhalb Sekunden", "1,5", "one and a half"), several options ("all", "keine"),
  and navigation words (skip/weiter, back/zurück, repeat/nochmal). It says "Okay." and moves on, shows what it
  heard, and after two misunderstandings stops listening until you act (no endless loops). Typing and clicking
  always work too.
- **Wake-word calibration in the wizard**: beep, record 2.5 s, three times; the calibration picks the lowest
  sensitivity that still detects your takes and learns how you pronounce the name (e.g. "Javis"), then reports
  "Recognized 3 of 3" (+ "try again" / pick another name if it went badly).
- **Downloads during the wizard**: speech models first (wake word 18 MB, VAD, speech output 129 MB, speech
  recognition 487 MB, voice lock 30 MB) so the wizard can speak and listen within a minute or two, then
  llama.cpp, then the language model for the chosen profile (auto -> its pick first, then the other one for the
  benchmark; smart/fast -> just that one). The plan is recomputed before every file, so changing "speed vs smart"
  mid-way changes what is downloaded next. Retries with backoff (10/30/90 s), disk-space check, progress + ETA in
  the wizard's footer; the language model starts loading as soon as it is on disk. Interrupted downloads resume
  at the next start. `ASSISTANT_NO_DOWNLOADS=1` turns this off (tests, offline PCs).
- **No wake listening during setup**: the always-on pipeline starts only when the wizard is finished (with the
  calibrated name), so the spoken questions can't trigger it; on a re-run it is paused meanwhile. Global hotkeys
  are paused while you record new ones.
- **Profile in the prompt**: name, language, personality, answer length, plus `profile.about` and
  `profile.location`; remembered facts newest first within a fixed budget (~1500 characters / 25 facts) so the
  system prompt stays short and cached - older facts are reachable with the `recall` tool.
- **Memory**: "what do you know about me / was weißt du über mich" lists facts (fast path, spoken as "You told
  me 3 things: …"); "forget that … / vergiss, dass …" deletes the clearly best match (asks which one if
  several match equally); near-identical facts are merged instead of duplicated; a correction is remembered as a
  new fact and the model is told to forget the old one first.
- **GUI tech**: the wizard is a normal window (QML + Qt Quick Controls "Basic" style, software renderer like
  the popup, dark title bar via DWM). It is loaded on demand and freed when it closes.

Milestone 6 decisions:

- **Settings window = ten tabs in QML** (General, Voice & wake word, AI model & performance, Permissions, Apps &
  aliases, Routines, History, Privacy, Appearance, Action log), dark/light like the wizard, English/German
  following `general.language`. Every change is saved immediately (no OK/Apply), validated by the same code as
  config.toml (invalid input shows the reason and the control snaps back). Things that only apply after a model
  restart say so and offer a Restart button.
- **Loaded on demand, freed when closed**: nothing of it exists until you open it; only the open tab is
  instantiated; closing deletes the window, its QML engine and all tab data, then trims the working set. Measured
  here: opening adds ~63 MB; after closing ~53 MB stays (29 MB Qt Quick library code + fonts that stay mapped once
  loaded, plus Qt's QML type caches) and it does **not** grow over repeated open/close cycles (5 cycles: flat).
  Idle with the window closed should stay under the 300 MB target (estimate: the ~205 MB idle measured in M4
  + ~53 MB after the first open = ~260 MB; to be confirmed on Windows).
- **Opening it**: tray menu "Settings…", double-click on the tray icon, or starting the app a second time.
  General has "Run the setup wizard again" plus shortcuts to config.toml, the data folder and the logs.
- **Voice tab** can calibrate the wake word (say the name 3x) and record the voice lock right there - the running
  wake word is paused meanwhile so it doesn't react to the takes - shows a live microphone level, previews each
  voice in English and German, plays a test sound on the chosen speaker, and changes devices without a restart.
- **Permissions tab**: Allow/Ask/Block per capability (admin and installs can't be "allow"), the always-ask list
  (admin is locked on), and folder rules (full / read only / always ask / blocked). Folder rules are compiled into
  the path checker, so a change now rebuilds it immediately - this also fixes the setup wizard, where new folder
  rules only took effect after a restart.
- **Routines tab**: list + editor (name, description, steps as action + JSON details, reorder, add/remove);
  every step is validated with the tool's own schema before saving; "Run now" runs it through the normal
  permission checks; templates (gaming mode, work mode, music time, good night).
- **History tab**: full-text search (FTS5, highlighted snippets), conversation view, rename, delete (click twice
  to confirm), export as Markdown or JSON. Lowering the retention deletes older conversations right away.
- **Privacy tab**: conversations on/off + retention + delete all; what the assistant remembers (edit, delete, add,
  forget everything); recordings; transcript logging. `privacy.save_audio` existed as a setting but did nothing -
  it now keeps a WAV of each voice request in `%LOCALAPPDATA%\Assistant\recordings` (off by default, same
  retention as conversations).
- **Action log tab**: every tool call with the permission decision (allowed / approved by you / declined /
  blocked / invalid / cancelled) and outcome; search, filters (asked, declined, blocked, errors), details per
  row, live updates while open, export CSV/JSON.
- **Appearance tab**: theme, accent (swatches or hex), popup position (picked on a mini screen), fade time,
  acrylic on/off, and a "Show a test popup" button. Theme/accent changes restyle open windows immediately.

Milestone 7 decisions:

- **Measured, not guessed**: `bench/perf_bench.py` measures every target of the spec (results and table in
  README → Performance; JSON in `%LOCALAPPDATA%\Assistant\bench\`). The stages were already logged as `TIMING`
  lines; the benchmark's "usage" section summarizes those from real use.
- **Idle tuning**: microphone blocks of 40 ms instead of 20 ms (the keyword spotter decodes ~320 ms chunks anyway;
  -12 % idle CPU measured, no latency cost), the level meter's RMS is computed only when a window reads it (not
  25x per second in the audio callback), and the 4 Hz wake-up that lets Ctrl+C work only runs with a console.
  Result here: 0.95 % CPU (all cores) and ~205 MB for the whole tray app.
- **Wake-word latency (~0.4 s vs. the 0.3 s target) is a model limit**: the detection comes 370-440 ms after the
  name ends, for all 10 test voices. Neither block size nor trailing-blank settings change it; sherpa-onnx has no
  smaller-chunk English keyword-spotting model. The popup appears 2-3 ms after the detection (pre-created
  window), and recording starts with 1.6 s of pre-roll, so nothing is lost - it just appears a little later.
- **Game mode** (automatic, `performance.game_mode_auto`, default on): every 3 s the foreground window is
  checked. It counts as a game when it covers its monitor and (in this order) it's in `performance.games`; not a
  browser/player/Office/chat/OBS/editor/RDP/launcher or in `performance.not_games`; and Windows reports exclusive
  D3D fullscreen (`SHQueryUserNotificationState`), or the exe lives in a game library folder (Steam, Epic, GOG,
  Xbox, Riot, Battle.net, EA, Ubisoft, Rockstar, …\Games\), or it has Direct3D/Vulkan/OpenGL loaded (psutil
  memory maps, cached per process). Two hits in a row (~6 s) enter game mode, 20 s without the game leave it
  (alt-tab tolerated). In game mode: llama-server stops (VRAM free), the voice stays on (CPU), simple commands
  work, a question that needs the model loads it on demand and it is unloaded again 2 min after the last answer;
  over an exclusive-fullscreen game the popup is not shown (it could minimize the game) - questions are spoken
  and answered by voice. The tray toggle overrides: on = stays on until switched off; off during a game = that
  game is ignored until it exits. A notification says when it switched and why.
- **Install = uv + venv, per user, no admin** (`install.bat` → `install.ps1`): copies the app to
  `%LOCALAPPDATA%\Programs\Assistant`, installs uv if missing, then `uv sync --frozen --no-dev --python 3.14`
  (exactly the versions in `uv.lock`), then `--setup --models auto` (llama.cpp + only the model that suits the
  GPU; the wizard can add the other one later), Start menu shortcuts, an "Installed apps" entry (HKCU) with
  uninstall, and starts the app. Re-running it updates in place: it asks the running app to quit (`--quit`,
  so llama-server and the logs close cleanly; anything left from the folder is ended), and keeps all data.
  Chosen over a PyInstaller-only distribution because it avoids antivirus false positives on the bootloader,
  updates are small, and it runs exactly the files that were tested.
- **`uninstall.ps1`**: quits the app, removes autostart, shortcuts and the uninstall entry, optionally the data
  (`%APPDATA%\Assistant`) and models/logs (`%LOCALAPPDATA%\Assistant`), then deletes the program folder.
- **Standalone .exe** (`scripts\build-exe.bat`): PyInstaller one-folder build with two programs sharing one folder
  (`Assistant.exe` without console, `AssistantCLI.exe` with console). Unused Qt parts are left out (other Quick
  Controls styles - all QML uses the Basic style -, QML dialogs, test modules, Qt translations and the software
  OpenGL DLL, since Qt Quick uses its software renderer): 372 → 326 MB on Linux. PyInstaller is pinned in the
  `build` dependency group of `uv.lock`. The icon is generated from the tray orb (`packaging/make_icon.py`).
- **`--self-test`**: compiles all 40 QML files and opens the setup wizard, the settings window (each tab) and
  the popup chat with a temporary data folder (no downloads, no autostart change), collecting QML warnings and
  errors; exit code 0/1. `build-exe.ps1` runs it on the fresh build; it's also the first troubleshooting step.
- **`--quit`**: asks the running instance (same data folder) to quit via the single-instance socket and waits
  until its lock is released.
- Messages about missing models name the right command for the installed variant (`assistant-cli --setup` vs.
  `AssistantCLI.exe --setup`) and the settings button.

Benchmark results here (4-core Xeon VM, CPU only, `bench/perf_bench.py`):

| Target | Measured | |
|--------|----------|---|
| Idle CPU < 2 % | 0.95 % of all cores (3.8 % of one core) | ✓ |
| Idle RAM < 300 MB | ~205 MB whole tray app (126 MB voice pipeline alone) | ✓ |
| Idle GPU unused | speech models on the CPU; GPU only for the LLM | ✓ |
| Wake word → popup < 300 ms | 405 ms median after the end of the name (max 440), popup +2-3 ms | ✗ model limit |
| End of speech → transcript < 500 ms | 284 ms (max 386) | ✓ |
| Fast-path command < 1 s | 0.6 ms median, p95 1.6 ms, 30/30 local | ✓ |
| LLM reply starts < 2 s | needs the GPU: `scripts\perf-bench.bat --llm` | ? |
| Speech recognition model load (only after idle unload) | 5.1 s, in parallel with your speech | |
| Answer → first spoken audio | 462 ms | |

Fixes after Milestone 7 (version 0.1.1, from the first install on Windows):

- **Speech recognition download failed every time**: the Parakeet archive on the k2-fsa/sherpa-onnx release was
  re-exported upstream on 2026-11-07 (487,211,443 bytes instead of 487,170,055; new encoder/decoder/joiner files).
  Every download then failed the pinned size/SHA-256 check *after* transferring 487 MB, four attempts per click.
  The pin now points to the new archive (checked: same file layout, identical transcripts of the EN/DE/FR/ES
  samples, all voice-pipeline tests pass). The other voice files are unchanged.
- **Downloader**: the size from the first response (Content-Length, or Content-Range when resuming) is compared with
  the pinned size, so a replaced file is noticed in under a second (`ServerFileChanged`). That error is not
  retried; the status line, the tray and `--setup` say to update the assistant. Same-size checksum mismatches are
  still retried (could be a broken transfer).
- **Hugging Face downloads use commit URLs** (`resolve/<commit>/…`, `REVISIONS` in llm/models.py), so a re-upload on
  a repository's main branch can't break the language models the same way. All pinned files were re-checked.
- `--setup` (install.bat step 4) continues after a failed item and lists what is missing at the end, instead of
  stopping at the first failure. Long download messages wrap in the wizard and settings.
- pypdfium2 5.15.0 was yanked upstream ("important correction" in 5.15.1) → 5.15.1 (PDF extraction re-checked).

Fixes in version 0.1.2 (from the first real Windows run):

- **llama.cpp download failed** ("no suitable llama.cpp Windows build in the latest release"): llama.cpp now
  publishes every build (b12110 …) as a *pre-release*, and its versioned releases - which GitHub's "latest" endpoint
  returns (v0.9.0) - contain no binaries, only `nightly-tag.txt` naming their build. `fetch_release` follows that
  pointer (v0.9.0 → b12110) and otherwise takes the newest non-draft release that has Windows builds (also covers a
  build whose files are still uploading). Checked against the real files: b12110 has the Windows CUDA 12.4 (+ runtime),
  Vulkan and CPU builds but no CUDA 13.5 build, so the CUDA 12.4 build is chosen (it runs on newer drivers too).
- **Closing the settings window with X/Esc** raised `TypeError: Cannot call meta function "void
  _on_closing(QQuickCloseEvent*)"`: PySide6 can't hand `QQuickCloseEvent` to a Python slot, so the cleanup never ran -
  the window was only hidden, and a wake word paused by the Voice tab could stay paused. Now QML's `onClosing` calls
  `settings.call("windowClosed")`. The tests and `--self-test` close it the way the X button does (they called the
  Python close before, which is why this wasn't caught).
- `--setup` also writes failed downloads to `cli.log` (they were only printed).

Version 0.1.3 (feedback: "make it work without push-to-talk", "it shouldn't look like this" - the taskbar showed
Windows' blank program icon):

- **App icon everywhere**: the app never set a window icon, so title bars, dialogs and the taskbar button showed
  Windows' blank program icon. New design (icons.py): a two-tone glowing orb (accent → analogous hue, violet → cyan by
  default) with a white voice waveform (3 bars below 24 px, 5 above); the same drawing is the tray icon (grey with pause
  bars when paused, ring while listening, game-mode badge), the window icon (`QApplication.setWindowIcon`, follows the
  accent colour) and `packaging/assistant.ico` (Start menu, Installed apps, .exe).
- **Hands-free from the start**: voice used to start only at launch or after *all* downloads (incl. the 6 GB language
  model), and the earlier download failures kept the speech models from installing, so only push-to-talk (= the typing
  box without voice) worked. Now listening starts as soon as the speech models are installed, a notification says
  "Ready - just say “<name>” …" once the microphone is really open (not at login), and the post-wizard "Ready" message
  is no longer shown when voice couldn't start.
- **Silent microphone warning**: Windows' privacy switch ("Let desktop apps access your microphone") makes the device
  deliver exact zeros - the wake word then never fires and nothing says why. After 6 s of digital silence a
  notification explains the setting (checked only until the first sound, so no idle cost).
- `--self-test` also checks that the app icon is set.

Version 0.1.4 (feedback: a gaming routine that "sounds like" a clip and starts everything for gaming):

- **Saying a routine's name runs it at once** (fast path, `match_routine`): "gaming mode", "start/activate/run …",
  "… on", "starte den …", "… an"; the whole sentence must be the name (fuzzy ≥ 90 for recognition slips like "gaming
  mod"). Before, routines only ran when the language model chose `run_routine`, i.e. not at all while it was missing.
- **"say" steps are spoken when they are reached**, through the same sentence stream the language model's answer uses
  (`Task.say`), so "Gaming mode activated." comes right away while Steam starts; the reply is the routine's own lines
  (or "<name>: done."). "Run now" in the settings speaks them too.
- **New step `play_sound`** (MP3/WAV/WMA/M4A, Windows MCI via winmm - no window, no extra package; own thread per
  sound, polling for stop): optional "wait until it ends"; folder rules apply to the file; not offered to the language
  model (`llm=False`). "Stop" and the panic hotkey stop sounds even after the routine has finished.
- **Routine editor**: friendly step names, a text field for "Say something", "Choose sound…" + "wait" switch for
  "Play a sound", seconds for "Wait"; JSON only for the other actions. Fixed: every keystroke rebuilt all step rows,
  which destroyed the field being typed in (one character, then the focus was gone) - text edits now change the step
  in place (regression test types into the field). The gaming template starts with "Gaming mode activated. Have fun!".
- The TikTok clip the request referred to couldn't be opened here (blocked); any sound file works as the intro.

Version 0.1.5 (feedback: "add something to update all my drivers"):

- **`driver_updates` tool** (tools/drivers.py; check | install) on the Windows Update Agent COM API
  (`Microsoft.Update.Session`, criteria `IsInstalled=0 and Type='Driver' and IsHidden=0`) through PowerShell - the
  official, signed drivers Windows offers for this hardware (same list as Optional updates → Driver updates).
  *check* needs no admin (capability terminal.read). *install* searches first so the confirmation shows the exact
  list, then needs a yes (capability software.install, risks install + admin - never "allow") and installs **only the
  listed update ids** in one elevated PowerShell (UAC) - ids are validated as GUIDs before they go into the script.
  It runs in the background (downloads take minutes; the assistant stays usable) with its own token, so "stop" never
  cuts an installation in half; a notification reports installed/failed/restart-needed (or a cancelled UAC prompt).
- Fast path EN/DE: "update (all) my drivers", "install the driver updates", "check for driver updates", "are my
  drivers up to date", "aktualisiere meine Treiber", "Treiber aktualisieren", "gibt es neue Treiber-Updates"...
- NVIDIA: the answer names the installed graphics driver and points to the NVIDIA App for the newest Game Ready
  driver (Windows Update usually lags behind). OEM tools (Lenovo Vantage, Dell Command Update, …) are not used.
- Tested with a fake PowerShell (search/installs/failures/cancelled UAC/errors); the WUA scripts themselves could not
  run here (no Windows) - first real run pending.

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

### Milestone 5 on Windows (setup wizard, profile, memory)

1. Fresh start: quit the assistant, rename `%APPDATA%\Assistant\config.toml` to `config.old.toml` (the models in
   `%LOCALAPPDATA%` stay), then `scripts\run-debug.bat`. The wizard opens; downloads run in its footer (with the
   models already there it says "All models are installed"). To test the downloads themselves, also rename
   `%LOCALAPPDATA%\Assistant\models\voice` - the speech models (~0.7 GB) come back while you answer.
2. Go through it by voice: switch on "Answer by voice", then answer naturally - "German", "call yourself Nova",
   say the name 3x after the beeps, "call me …", "skip", "the second one", "one and a half seconds", "back",
   "repeat". Also try typing/clicking, ▶ voice previews, the mic level meter (and switching microphones), adding
   a trusted folder, changing a permission, recording a new hotkey (click "Change", press the keys), the voice
   lock (read 3 sentences), and a routine template.
3. Close the window half-way -> "use the suggestions for the rest?" -> yes. Then tray menu -> **Setup wizard…**
   runs it again with your current settings as defaults.
4. After finishing: "Ready - just say Nova…" notification; the wake word uses the calibrated sensitivity
   (`:config voice` in the CLI shows `wake_sensitivity` / `wake_variants`); routines exist ("Nova, gaming mode").
5. Memory: "Nova, remember that my sister's name is Lea" -> "Nova, what do you know about me?" ->
   "Nova, forget that my sister's name is Lea". Ask the LLM something personal ("what's my sister called?").
   With the profile ("about you") filled in, ask "what should I cook tonight?" - it should take it into account.
6. Text version (no GUI): `scripts\run-cli.bat` -> `:setup` (Enter keeps a suggestion, `back`, `quit`).
7. Please report: which spoken answers it misunderstood (the "I heard: …" line shows the transcript), anything
   that looks off in the wizard, and whether the downloads/ETA behaved.

### Milestone 6 on Windows (settings window)

1. `scripts\run-debug.bat`, then open the settings: tray menu "Settings…", double-click the tray icon, or start
   `scripts\run-tray.bat` again while it runs.
2. Go through the tabs; every change is saved at once (watch `config.toml` / `permissions.toml`):
   - General: change your name/personality, the language (the window switches to German), autostart.
   - Voice: "Calibrate: say the name 3x"; talk and watch the level bar; switch microphone; ▶ voice previews;
     record the voice lock, then delete it; record a new push-to-talk key.
   - AI model: switch profile/models, then Restart; "Download missing models" if something is missing.
   - Permissions: set "Mouse & keyboard" to Allow, add a trusted folder and ask the assistant to create a file
     there (no question now), set "Delete files" to Block and ask it to delete something (refused).
   - Apps: add an alias ("post" → Outlook) and say "open post"; desktop rules.
   - Routines: create one from a template, edit a step (try invalid JSON - you get the reason), Run now.
   - History: search a word you said earlier, rename, export Markdown/JSON, delete.
   - Privacy: edit/delete remembered facts; turn on recordings, make a voice request, "Open folder" shows the WAV.
   - Appearance: accent colour + popup position, then "Show a test popup".
   - Action log: the actions you just triggered, with filters and CSV export.
3. Close the window and check Task Manager: the memory goes back down (not to the exact starting value - the
   Qt Quick libraries stay loaded - but it must not grow when you open/close the settings repeatedly).
4. Please report: anything that doesn't save or doesn't take effect, layout problems at your scaling (125/150 %),
   and whether the dark title bar and the Segoe Fluent icons in the sidebar look right.

### Milestone 7 on Windows (installer, performance, game mode, .exe)

1. **Install**: unzip `assistant-m7.zip` (e.g. to Downloads), double-click `install.bat`. Watch the six steps;
   it should end with "Started." and the setup wizard (or the tray icon if you set it up before - your existing
   `%APPDATA%\Assistant` settings are used). Check: Start menu → *Assistant* (4 entries), *Settings → Apps →
   Installed apps* shows "Assistant (local voice assistant)" with size and version. Run `install.bat` again
   (= update): the running app quits by itself, nothing is lost. Options: `install.bat -SkipModels -NoLaunch`.
2. **Self-test**: `"%LOCALAPPDATA%\Programs\Assistant\.venv\Scripts\assistant-cli.exe" --self-test` → seven
   "ok" lines and "self-test passed" (windows flash briefly).
3. **Performance**: `scripts\perf-bench.bat --llm` in the install folder (or `uv run python bench\perf_bench.py
   --llm` in the source) → table + JSON. For the real idle numbers let the tray app sit idle for a few minutes,
   then `scripts\perf-bench.bat --attach --idle-seconds 120 --skip wake,stt,tts,fastpath`. Please send both tables.
4. **Game mode**: start a fullscreen game. After ~6 s a notification "Game mode (<game>.exe)"; Task Manager →
   GPU memory drops (llama-server is gone). "Jarvis, set the volume to 20" works; a real question loads the model
   (slower first answer) and 2 minutes later it is unloaded again. Alt-tab out and back within 20 s → stays in
   game mode; quit the game → ~20 s later it ends and the model reloads. In an exclusive-fullscreen game no popup
   appears (voice only). A YouTube video in fullscreen must *not* trigger it. Try the tray toggle (on/off by hand)
   and *Settings → AI model → Always games / Never games*.
5. **Standalone .exe** (in the source folder): `scripts\build-exe.bat -Zip` → builds `dist\Assistant\`, runs the
   self-test, writes `dist\Assistant-0.1.0.zip`. Unzip it somewhere else and start `Assistant.exe` (quit the
   installed one first - both use the same data); `AssistantCLI.exe --setup` and `AssistantCLI.exe` (debug CLI).
   Note what SmartScreen / your antivirus say.
6. **Uninstall**: *Installed apps → Assistant → Uninstall* (or Start menu → *Uninstall Assistant*): answer the
   questions about data and models; afterwards the Start menu entries, the autostart entry
   (`reg query HKCU\Software\Microsoft\Windows\CurrentVersion\Run /v Assistant`) and the program folder are gone.
7. Please report: anything the installer printed in yellow/red (+ `%TEMP%\assistant-install.log`), the two
   benchmark tables, how game mode behaved in your games, and the .exe build result.

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
- M5 open points: the wizard GUI was rendered and clicked through offscreen here (all 27 pages, EN/DE, dark/light,
  no QML warnings) and its voice flow was tested with the real models, but it has not run on Windows yet (focus
  handling, dark title bar, file dialog, hotkey capture). German tool/capability titles exist only in the
  wizard so far; permission questions in the popup still show the English summaries. Unusual names can be
  misheard when answered by voice (in tests "Lennox" was transcribed as "Linux" half of the time) - the wizard
  shows "I heard: …", so such a name is best typed.
- M6 open points: the settings window was loaded and exercised offscreen (all tabs, both languages, both themes,
  no QML warnings; every action covered by tests) but has not run on Windows yet. ~53 MB stay resident after
  closing it (libraries + Qt caches, not growing); a separate process would be needed to give that back too.
- M7 open points: `install.ps1`, `uninstall.ps1` and `build-exe.ps1` were written for Windows PowerShell 5.1
  and reviewed by hand, but could not be run or even syntax-checked by PowerShell here (no PowerShell in the
  container, and running a downloaded PowerShell build was not allowed) - expect possible small fixes on the
  first run. The PyInstaller build was built and passed `--self-test` on Linux only; the Windows build has the
  same spec (with the Windows-only modules added). Game-mode detection is Windows-only code (type-checked;
  classification and hysteresis unit-tested with a fake probe). The wake word reacts ~0.4 s after the name
  (target 0.3 s; model limit). The LLM first-token target (< 2 s) still has to be measured on the RTX 4070.
  The program is not code-signed (SmartScreen warning for the .exe; the uv/Python install avoids it).
- All seven milestones are built. Next would be the first real-Windows round: run the per-milestone checklists
  above and fix what comes up.
