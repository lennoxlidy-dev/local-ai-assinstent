# Assistant: a local voice assistant for Windows

You call it by a name you choose ("Jarvis, …"). It controls your PC (apps, windows, files, music, volume,
timers, the screen) and answers questions. Speech recognition, the language model (llama.cpp) and speech
output all run on your own PC. There are no cloud services and no telemetry. The internet is only used to
download the program and its models.

- **Instant commands** without the language model: volume, media keys, "play …", open/close apps, timers and
  reminders, virtual desktops, brightness, time/date, lock, "stop". English and German.
- **Everything else** goes to a local language model (Qwen3.5 9B or 4B) that uses the same tools. For example:
  find, read and summarize files; create, move or delete files (deleted files go to the Recycle Bin); run
  terminal commands; read the screen (UI Automation, OCR, vision); remember facts about you; run routines
  such as "gaming mode".
- **Safety is enforced in the code**: Allow / Ask / Block per capability and per folder. Risky actions always
  ask first and show the exact command or paths, and you answer by voice or by clicking. Text from files, web
  pages and the screen is treated as data, never as instructions. There is a full action log, and a panic
  hotkey cancels everything.
- **Setup wizard** on the first start (27 short questions, by voice or by typing), a **settings window** with
  ten tabs, a searchable **history**, and an optional **voice lock** so that videos or other people can't
  trigger it.
- **Game mode**: when a fullscreen game runs, the language model is unloaded so the game gets all of the
  graphics card's memory.

## Requirements

| | |
|---|---|
| Windows | Windows 10 (1809 or newer) or Windows 11, 64-bit |
| Disk | about 11 GB free for a normal install (program and Python ≈ 1 GB, llama.cpp, speech models 0.7 GB, one language model 3–6 GB); 15 GB for both models |
| Graphics | an NVIDIA GPU with 8 GB VRAM is ideal (RTX 3060/4060/4070 or similar). With 6 GB it picks the smaller model. Other GPUs use the Vulkan build, and with no GPU it runs on the CPU (much slower answers). |
| Audio | a microphone and speakers. Headphones work best because there is no echo cancellation. |
| Internet | for the installation and the model downloads (≈ 4–7 GB, resumable) |

## Installation (one click)

1. Unzip the folder anywhere (for example to Downloads).
2. Double-click **`install.bat`**. It installs per user, so no administrator rights are needed. The installer:
   1. checks Windows, disk space and the graphics card;
   2. copies the app to `%LOCALAPPDATA%\Programs\Assistant`;
   3. installs [uv](https://docs.astral.sh/uv/) if it is missing, then Python 3.14 and all packages into a
      private `.venv` (your system's Python is not touched);
   4. downloads llama.cpp (the CUDA build that matches your NVIDIA driver), the language model that suits your
      graphics card, and the speech models;
   5. creates Start menu entries and an "Installed apps" entry;
   6. starts the assistant. The setup wizard opens.

   The first installation takes a few minutes plus the downloads. A log is written to `%TEMP%\assistant-install.log`.

Options (pass them to `install.bat` or `install.ps1`):

| Option | Effect |
|---|---|
| `-SkipModels` | don't download now. The setup wizard downloads everything in the background instead. |
| `-AllModels` | download both language models (smart 9B and fast 4B), so you can switch between them in the settings |
| `-DesktopShortcut` | also put a shortcut on the desktop |
| `-InstallDir D:\Apps\Assistant` | install somewhere else |
| `-NoLaunch` / `-NoPause` | don't start the app at the end / don't wait for Enter (unattended installs) |

**Update:** run `install.bat` from the new version. Settings, memory, conversations and models are kept. A
running assistant is closed first.

**Uninstall:** go to *Settings → Apps → Installed apps → Assistant (local voice assistant) → Uninstall*, or
use *Start menu → Assistant → Uninstall Assistant*. The uninstaller asks whether to also delete your data
and the downloaded models. If you say no, they stay.

### Where things are

| What | Where |
|---|---|
| Program | `%LOCALAPPDATA%\Programs\Assistant` |
| Settings (`config.toml`), permissions (`permissions.toml`), memory, conversations, action log (`assistant.db`) | `%APPDATA%\Assistant` |
| Language and speech models, llama.cpp, logs | `%LOCALAPPDATA%\Assistant` (`models\`, `bin\`, `logs\`) |

The tray menu (*Open folder*) and *Settings → General* open these folders for you.

### Alternative: standalone program folder (no Python on the target PC)

On a PC where the source is unpacked, run **`scripts\build-exe.bat`** (add `-Zip` to also get a zip file).
It builds `dist\Assistant\` with **`Assistant.exe`** (the tray app) and **`AssistantCLI.exe`** (console:
`--setup`, `--self-test`, the debug CLI). Afterwards it runs the self-test, which opens every window once to
make sure nothing is missing from the build. You can copy the folder anywhere and start `Assistant.exe`. The
setup wizard downloads the models on the first start. The program is not code-signed, so Windows SmartScreen
may warn: click *More info → Run anyway*. The data folders are the same as above.

## First start: the setup wizard

The wizard asks one question per page: language, the assistant's name, saying the name 3 times
(this calibrates the wake word to your voice), what to call you, voice and personality, microphone, music app
and browser, folders, permissions, autostart, hotkeys, game mode, fast vs. smart model, privacy, voice lock
and routines. Each question is read aloud and can be answered by voice ("the second one", "skip", "back",
"one and a half seconds"), by typing or by clicking. Every question has a sensible default; skipping a
question uses the default. The models download in the background while you answer.

Run the wizard again at any time from the tray menu (**Setup wizard…**). Every answer can also be changed in
the settings window.

## Using it

Say the name, then your request, in one go or with a pause: "Jarvis … open Notepad". A popup at the top of the
screen shows what it heard and the answer. The popup never takes the keyboard focus. Right after an answer it
listens for 5 seconds for a follow-up without the name.

| Say | What happens |
|---|---|
| "Jarvis, set the volume to 30" / "Jarvis, Lautstärke auf 40" | instant |
| "Jarvis, play Bohemian Rhapsody" / "next song" / "pause" | Spotify (or your music app) |
| "Jarvis, open Notepad" / "close Spotify" / "switch to desktop 2" | instant |
| "Jarvis, set a timer for 5 minutes" / "remind me in 20 minutes to call Mom" | popup and alarm sound when due |
| "Jarvis, what time is it?" / "wie spät ist es?" | instant |
| "Jarvis, find my newest PDF in Downloads and tell me what it's about" | language model + file tools |
| "Jarvis, create a file shopping.txt on the desktop with milk, eggs, bread" | asks first and shows the exact path |
| "Jarvis, what's on my screen?" | UI Automation / OCR / vision |
| "Jarvis, remember that my sister's name is Lea" / "what do you know about me?" / "forget that …" | memory |
| "Jarvis, gaming mode" / "start gaming mode" | runs your routine instantly (see [Routines](#routines)) |
| "Jarvis, update my drivers" / "check for driver updates" | Windows Update: lists new drivers, asks, then installs (see [Driver updates](#driver-updates)) |
| "Jarvis, stop" | stops talking and cancels the current request |

- **Push-to-talk:** `Ctrl+Alt+Space` (no name needed). **Panic:** `Ctrl+Alt+Esc` silences it, cancels
  everything and releases the mouse and keyboard. Change both in *Settings → Voice & wake word*.
- **Typing:** click the popup, or use the tray menu's *Type a request…*, to open a small chat.
- **Questions before risky actions:** the popup shows the exact command or paths. Say "yes" / "no" (also
  "ja" / "nein", "always", "for this task") or click. Whichever comes first counts, and anything unclear
  counts as no.
- **Tray menu:** status, *Type a request…*, *Settings…*, *Setup wizard…*, *Pause listening* (this really
  closes the microphone), *Game mode*, *Start at login*, *Open folder*, *Quit*. Double-clicking the icon
  or starting the app a second time opens the settings.
- **Settings window:** General · Voice & wake word · AI model & performance · Permissions · Apps & aliases ·
  Routines · History · Privacy · Appearance · Action log. Every change is saved immediately.

## Routines

A routine starts several things with one sentence, e.g. **"Jarvis, gaming mode"**. Say its name, alone or with
"start" / "activate" / "… on" ("starte den Spielmodus", "Spielmodus an" in German). It runs immediately, even
without the language model. Create routines in the setup wizard, from a template, or in *Settings → Routines*:

| Step | What it does |
|---|---|
| **Say something** | speaks a sentence the moment the routine reaches it, e.g. "Gaming mode activated. All systems online." |
| **Play a sound** | plays an MP3, WAV, FLAC or OGG file (WMA/M4A through Windows' own player), no player window. Click **Choose sound…**, then choose how it ends: **Plays once**; **Plays once, then the next step**; or **Keeps playing until the routine is done** (repeats if it's shorter, fades out when the last step is done). **at least … s** makes it play at least that long (a short sound repeats); **volume** in % |
| **Check / update drivers** | **Only check** says which driver updates Windows Update has; **Install** asks first and shows the Windows administrator prompt (see [Driver updates](#driver-updates)) |
| **Open an app** | e.g. Steam, Discord, your game launcher (`{"name": "Steam"}`) |
| **Volume**, **Media**, **Play music**, **Virtual desktop**, **Wait**, … | everything the assistant can do, with the same permission checks |

Example: a gaming routine that sounds like a movie AI:

1. *Settings → Routines → From template → Gaming mode* (Steam, Discord, volume 40 % and "Gaming mode activated").
2. **+ Add step → Play a sound**, then click **Choose sound…** and pick your intro sound (any MP3/WAV on your PC).
   Move it to the top with ↑, choose **Keeps playing until the routine is done**, and set e.g. **at least 10 s**, so the
   music plays while everything starts and fades out at the end.
3. Change the **Say something** text to your own line, add more **Open an app** steps (e.g. your game), then **Save**.
4. **▶ Run now** tries it, or just say **"Jarvis, gaming mode"**.

A maintenance routine could be: **Say something** "Checking your drivers", then **Check / update drivers → Only
check**. It says the result, e.g. "2 driver updates available: …".

While the assistant talks, a routine's sound is turned down automatically, to 25 % by default (*Settings → Voice →
Sounds while I'm talking*), so you can hear it over the music. *Voice volume* there makes the voice itself louder.

"Jarvis, stop" or the panic hotkey also stops a routine's sound. The voice of the spoken lines is chosen in
*Settings → Voice* (10 voices: F1–F5, M1–M5).

## Driver updates

Say **"Jarvis, update my drivers"**, or "check for driver updates" to only see what's available ("aktualisiere meine
Treiber" in German). The assistant asks **Windows Update** for new drivers for your hardware. That's the same official,
signed source as *Windows Settings → Windows Update → Advanced options → Optional updates → Driver updates*.

1. It shows the exact list (name, date, size) and asks you first. Updates are never installed without your yes.
2. After your yes, Windows shows its administrator (UAC) prompt, and only the listed updates are installed.
3. Installing runs in the background, so you can keep using the assistant. A notification says when it's done and
   whether a restart is needed. A running installation is never interrupted, not even by "stop".

Your **NVIDIA graphics driver**: Windows Update usually offers an older version. The newest Game Ready driver comes
from the NVIDIA App ("Jarvis, open NVIDIA App"). The answer tells you which driver version you have.

## Privacy and safety

- **Fully local.** The internet is only used for downloads: the installer (astral.sh for uv, PyPI for the
  Python packages, a Python build), llama.cpp (github.com) and the models (huggingface.co, github.com). The
  language model server listens only on `127.0.0.1` and uses a random key, so web pages can't use it.
- **Microphone audio stays in memory** and is never written to disk, unless you turn on recordings in
  *Settings → Privacy* (off by default). Transcripts are not written to the logs unless you enable that.
- **Conversations** are stored locally (searchable in *History*) for 90 days by default. You can change the
  retention or switch saving off.
- **Permissions** (*Settings → Permissions*): Allow / Ask / Block per capability, plus folder rules. The
  defaults: reading is allowed on all drives; changes need your confirmation; Windows, Program Files and
  ProgramData are read-only; SSH keys, credential stores and browser profiles are blocked. Deleting, moving,
  overwriting, terminal commands that change things, installs, power actions and closing apps with unsaved work
  always ask. Administrator rights are requested only through the normal Windows UAC prompt, after asking you.
  Some things are always refused, such as deleting a drive root or your profile folder.
- **Prompt injection:** text from files, web pages, the clipboard and the screen reaches the model only as
  marked data. After the model has read such data, every action in that request asks first, even ones set to
  "Allow".
- **Action log:** every tool call, with the permission decision and the result, is in *Settings → Action log*
  (searchable, exportable).
- **Voice lock** (optional, *Settings → Voice*): only your voice is accepted. It stores a voiceprint of 192
  numbers, no audio.

## Performance

The targets from the spec, and what was measured in the development container (a 4-core Xeon VM, CPU only).
To check them on your PC, run `scripts\perf-bench.bat` (add `--llm` for the language model, or
`--attach --idle-seconds 120` to measure the running tray app):

| Target | Measured | |
|---|---|---|
| Idle: < 2 % CPU | 0.95 % (Task Manager style, all cores); 3.8 % of one core | ✓ |
| Idle: < 300 MB RAM | about 205 MB for the full tray app (126 MB voice pipeline alone); speech recognition (~0.9 GB) loads at the wake word and unloads after 10 idle minutes | ✓ |
| Idle: GPU unused | the speech models run on the CPU; the GPU is only used by the language model | ✓ |
| Wake word → popup < 300 ms | **≈ 400 ms** after the name ends. The keyword-spotting model decides in fixed ~0.4 s steps; the popup itself appears 2–3 ms after detection | ✗ (model limit) |
| End of speech → transcript < 500 ms | 284 ms after the 1 s pause that marks the end | ✓ |
| Fast-path command done < 1 s | 0.6 ms (+ the time the app or tool needs), 30/30 common commands | ✓ |
| LLM reply starts < 2 s | must be measured on your GPU: `scripts\perf-bench.bat --llm` | ? |

Per-stage timings of real requests are in `%LOCALAPPDATA%\Assistant\logs\assistant-debug.log` (`TIMING` lines).
`perf-bench` also summarizes those timings.

- **Language model memory:** it stays loaded by default, so answers are fast. *Settings → AI model →
  Unload the model when idle* (in minutes; 0 = never) frees the VRAM after a while; it reloads on the next question (a few seconds).
- The listening loop runs as a low-priority efficiency (EcoQoS) thread.

## Game mode

When a **fullscreen game** has been in the foreground for about 6 seconds, the assistant:

- unloads the language model, so the game gets all of the VRAM;
- keeps working by voice. Simple commands still work. A bigger question loads the model on demand, and it is
  unloaded again 2 minutes after the last answer;
- doesn't show the popup over **exclusive-fullscreen** games (it could minimize them). Questions are then asked
  and answered by voice only.

It leaves game mode about 20 seconds after the game is closed or minimized (alt-tab is tolerated), and the
model reloads in the background.

A fullscreen window counts as a game if Windows reports exclusive fullscreen, if the program is installed in a
game library (Steam, Epic, GOG, Xbox, Battle.net, EA, Ubisoft, Riot, …), or if it renders with Direct3D,
Vulkan or OpenGL. Browsers, video players, Office, Teams/Zoom/Discord, OBS, editors and remote desktops are
never games. If the detection is wrong, add the program (for example `eldenring.exe`) to *Always games* or
*Never games* in *Settings → AI model*. The tray menu's *Game mode* switch forces it on or off by hand, and
switching it off during a game ignores that game until it closes.

## Troubleshooting

**The installer doesn't start or stops right away.** Start it with `install.bat`, not by right-clicking
`install.ps1`. The .bat file allows the script to run (`-ExecutionPolicy Bypass`) without changing your
system settings. Read `%TEMP%\assistant-install.log` for the details. The most common causes:

- no internet or a proxy (the downloads of uv/Python/packages fail);
- an antivirus program that blocks `uv.exe`;
- too little disk space.

You can run it again at any time; finished steps and downloads are reused.

**"Windows protected your PC" / the antivirus removes `Assistant.exe`.** The program isn't code-signed, and
programs built with PyInstaller sometimes trigger false alarms. Click *More info → Run anyway*, or restore the
file and allow it in your antivirus. The `install.bat` installation avoids this because it runs the normal
Python.

**No tray icon.** Look under the **^** arrow in the taskbar (*Taskbar settings → Other system tray icons* can
pin it). Task Manager shows whether it runs (`pythonw.exe` or `Assistant.exe`). Start *Start menu → Assistant →
Assistant with log window* to see error messages live.

**It doesn't react to the name.**
- In *Settings → Voice*, speak and watch the level bar. If nothing moves, choose the right microphone and
  check *Windows Settings → Privacy & security → Microphone → "Let desktop apps access your microphone"*.
- If the tray icon is grey, listening is paused.
- *Calibrate: say the name 3x* (in *Settings → Voice*) tunes the sensitivity to your voice. You can also raise the sensitivity slider.
- Names with 2–3 syllables work best. The wake-word model was trained on English, so a German pronunciation of
  an English name is recognized less reliably; calibration helps.
- Push-to-talk (`Ctrl+Alt+Space`) always works.

**It reacts to the TV or to other people.** Lower the sensitivity, turn on the voice lock, or choose a less
common name. Every trigger is double-checked against the transcript, so most near-misses are already ignored.

**It misunderstands requests.** The popup shows what it heard. Speak after the name, in one sentence, and
avoid background music. For unusual names or words, typing in the popup chat is more reliable.

**No sound, or the wrong speaker.** In *Settings → Voice*, choose the output device and play the test sound.

**The voice is too quiet** (or drowned out by music). Raise *Settings → Voice → Voice volume* (up to 200 %, softly
limited so it doesn't distort). Lower *Sounds while I'm talking* to make a routine's sound duck further.

**"The language model isn't available" / only simple commands work.** The model or llama.cpp is still
downloading or is missing. Use *Settings → AI model → Download missing models*, or run
`scripts\setup-models.bat` (standalone build: `AssistantCLI.exe --setup`). Errors of the model server are in
`%LOCALAPPDATA%\Assistant\logs\llama-server.log`.

**Answers are slow.**
- While it answers, *Task Manager → Performance → GPU* should show activity. If it doesn't, update the NVIDIA
  driver: the CUDA 13 build needs driver 580 or newer, otherwise the CUDA 12.4 build is used. Then run
  `setup-models.bat` again.
- A running game or video editor may be using the VRAM. Try the *fast* model (*Settings → AI model*).
- Run `scripts\perf-bench.bat --llm` to see the numbers.

**Downloads fail** (firewall/proxy). Allow `github.com`, `huggingface.co` and their download servers. Downloads
resume where they stopped and are checked with SHA-256. For a PC without internet, copy
`%LOCALAPPDATA%\Assistant\models` and `bin` from another PC. Setting the environment variable
`ASSISTANT_NO_DOWNLOADS=1` stops all download attempts.

**"The file on the download server has changed."** Every download is checked against the exact size and checksum
this version expects. If a model's publisher replaces a file, the check fails and nothing is installed (on
purpose: an unverified file is never used). Get the latest version of the assistant from GitHub (*Code →
Download ZIP*), extract it and run `install.bat`; settings and the other models are kept.

**The popup looks wrong** (black corners, no blur). Turn off *Settings → Appearance → Blurred (acrylic)
background*.

**A hotkey does nothing.** Another program probably uses the same combination. Record a different one in
*Settings → Voice & wake word*.

**Game mode turns on or off at the wrong time.** Use the *Always games* / *Never games* lists, see
[Game mode](#game-mode).

**A window stays empty, or the app won't start after an update.** Run the self-test. It opens every window
once with a temporary data folder and prints what is broken:

```bat
"%LOCALAPPDATA%\Programs\Assistant\.venv\Scripts\assistant-cli.exe" --self-test
```

For the standalone build, run `AssistantCLI.exe --self-test`. Reinstalling (`install.bat`) repairs the
program files.

**Start over.** Quit the assistant (tray → *Quit*).
- Rename `%APPDATA%\Assistant\config.toml` to reset the settings; the wizard runs again.
- Rename `permissions.toml` to get the default permissions back.
- Delete the whole `%APPDATA%\Assistant` folder to also forget memory and conversations. The models in
  `%LOCALAPPDATA%` stay.

**Logs** for a bug report are in `%LOCALAPPDATA%\Assistant\logs\`:

| File | Contents |
|---|---|
| `assistant.log` | the main log |
| `assistant-debug.log` | details and `TIMING` lines |
| `llama-server.log` | the language model server |
| `crash.log` | should be empty |
| `cli.log` | the debug CLI |

They contain no transcripts unless you turned that on.

## For developers

| Command | What it does |
|---|---|
| `powershell -ExecutionPolicy Bypass -File scripts\dev-setup.ps1` | installs uv if needed, then `uv sync` (Python 3.14 + packages into `.venv`) |
| `uv run pytest` | runs the tests (tests that need the real models are skipped if they aren't installed) |
| `scripts\run-tray.bat` / `scripts\run-debug.bat` | the tray app / the same with a live log |
| `scripts\run-cli.bat` | text-mode debug CLI (`:help`): every tool, the permission gate, `:calibrate`, `:enroll`, `:setup` |
| `scripts\setup-models.bat` | llama.cpp + models (`python -m assistant --setup [--models auto\|id,id]`) |
| `scripts\perf-bench.bat` | the performance targets (`bench\perf_bench.py`) |
| `uv run python bench\llm_bench.py` | language-model accuracy and latency per model (sets what `llm.profile = "auto"` uses) |
| `scripts\build-exe.bat [-Zip]` | the standalone `dist\Assistant\` folder (PyInstaller, `packaging\assistant.spec`) |

Command line (`python -m assistant …`, `assistant.exe`, `Assistant.exe`):

| Option | Effect |
|---|---|
| `--cli` | the debug CLI |
| `--debug` | log to the console |
| `--home DIR` | keep all data in DIR (portable / testing) |
| `--onboarding` | open the setup wizard |
| `--setup [--models auto\|ids]` | download llama.cpp and the models |
| `--say TEXT --out WAV` | synthesize speech with its voice |
| `--voice-test WAV…` | run WAV files through the real voice pipeline |
| `--self-test` | open every window once with a temporary home |
| `--quit` | ask the running instance to quit |
| `--smoke-test N` | start and quit after N seconds |
| `--version` | print the version |

[`PROGRESS.md`](PROGRESS.md) has the architecture, every design decision with its reasons, the
benchmark results and per-milestone test instructions. [`SPEC.md`](SPEC.md) is the original specification.
