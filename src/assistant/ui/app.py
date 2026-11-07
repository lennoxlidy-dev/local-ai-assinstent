"""Tray application bootstrap (the default entry point)."""

from __future__ import annotations

import logging
import os
import shutil
import signal
import sys
import tempfile
import threading
import time
from typing import Any

from PySide6.QtCore import QFileSystemWatcher, QObject, Qt, QTimer, QtMsgType, Signal, qInstallMessageHandler
from PySide6.QtWidgets import QApplication

from .. import APP_ID, APP_USER_MODEL_ID, __version__
from ..core.context import AppContext, bootstrap
from ..core.events import Event, Topic
from ..win.hotkeys import Binding, HotkeyManager
from ..win.shell import open_path, set_app_user_model_id
from .popup import PopupController
from .single_instance import SingleInstance
from .tray import TrayController

log = logging.getLogger(__name__)
qtlog = logging.getLogger("qt")


def _qt_message(mode: QtMsgType, _ctx: Any, message: str) -> None:
    level = {QtMsgType.QtDebugMsg: logging.DEBUG, QtMsgType.QtInfoMsg: logging.INFO,
             QtMsgType.QtWarningMsg: logging.WARNING, QtMsgType.QtCriticalMsg: logging.ERROR,
             QtMsgType.QtFatalMsg: logging.CRITICAL}.get(mode, logging.INFO)
    qtlog.log(level, "%s", message)


def apply_theme(app: QApplication, theme: str) -> None:
    hints = app.styleHints()
    if hasattr(hints, "setColorScheme"):  # Qt >= 6.8
        scheme = {"dark": Qt.ColorScheme.Dark, "light": Qt.ColorScheme.Light}.get(theme, Qt.ColorScheme.Unknown)
        hints.setColorScheme(scheme)


class TrayApp(QObject):
    _answer = Signal(str, bool)
    _open_chat = Signal()
    _hotkeys_changed = Signal()
    _setup_event = Signal(object)
    _game_unload = Signal(bool)

    def __init__(self, ctx: AppContext, app: QApplication, *, autostarted: bool = False, open_setup: bool = False):
        super().__init__()
        self.ctx = ctx
        self.app = app
        self.autostarted = autostarted
        self.force_setup = open_setup
        self.tray = TrayController(ctx)
        self.tray.quit_requested.connect(self.quit)
        self.tray.settings_requested.connect(self.open_settings)
        self.tray.setup_requested.connect(lambda: self.open_setup(first_run=False))

        # Pick up manual edits of config.toml (editors save by replacing the file -> re-add the path).
        self.watcher = QFileSystemWatcher([str(ctx.paths.config_file)])
        self.watcher.fileChanged.connect(self._on_config_file_changed)
        self._reload_timer = QTimer(singleShot=True, interval=300)
        self._reload_timer.timeout.connect(self._reload_config)

        self.tools = None
        self.assistant = None
        self.llm = None
        self.voice: Any = None            # VoiceStack once the voice models are loaded
        self.confirmer: Any = None
        self.setup_window: Any = None     # the setup wizard while it is open
        self.settings_window: Any = None  # the settings window while it is open
        self.downloads: Any = None        # SetupManager (background model downloads)
        self.popup = PopupController(ctx, on_submit=self._submit)
        self.hotkeys = HotkeyManager()
        self.tray.ask_requested.connect(self.ask_dialog)
        self._answer.connect(self._show_answer)
        self._open_chat.connect(self.ask_dialog)
        self._hotkeys_changed.connect(self._register_hotkeys)
        self._setup_event.connect(self._on_setup_event)
        self.game_watcher: Any = None
        self._unload_timer = QTimer(self, singleShot=True, interval=120_000)
        self._unload_timer.timeout.connect(self._unload_after_game_request)
        self._game_unload.connect(self._game_unload_timer)
        ctx.bus.subscribe("assistant.reply", self._on_reply)
        ctx.bus.subscribe(Topic.STATE_CHANGED, self._on_state_changed)
        ctx.bus.subscribe(Topic.CONFIG_CHANGED, self._on_config_changed)
        ctx.bus.subscribe(Topic.NOTIFY, self._on_notify)
        for topic in ("setup.job_done", "setup.finished"):
            ctx.bus.subscribe(topic, self._setup_event.emit)

    def _on_state_changed(self, event: Event) -> None:
        """Pause closes the microphone; game mode frees the GPU (model stopped, reloaded afterwards)."""
        changed = event.data.get("changed", {})
        if "paused" in changed and self.voice is not None:
            threading.Thread(target=self._set_voice_paused, args=(bool(changed["paused"]),),
                             name="voice-pause", daemon=True).start()
        if "game_mode" not in changed or self.llm is None:
            return
        llm = self.llm
        if changed["game_mode"]:
            threading.Thread(target=llm.stop, name="llm-stop", daemon=True).start()
            game = self.ctx.state.game_reason
            what = f" ({game})" if game and game != "manual" else ""
            self.ctx.bus.publish(Topic.NOTIFY, title="Game mode" + what,
                                 message="Language model unloaded - the graphics card is free. Simple commands "
                                         "still work; bigger questions load it on demand.")
        else:
            self._game_unload.emit(False)
            self._maybe_start_llm()

    def _on_reply(self, event: Event) -> None:
        """In game mode the model is only loaded on demand - unload it again after a quiet spell."""
        if event.data.get("via") == "llm" and self.ctx.state.game_mode:
            self._game_unload.emit(True)

    def _game_unload_timer(self, start: bool) -> None:
        if start:
            self._unload_timer.start()
        else:
            self._unload_timer.stop()

    def _unload_after_game_request(self) -> None:
        if self.ctx.state.game_mode and self.llm is not None and self.llm.ready:
            log.info("game mode: unloading the language model again")
            threading.Thread(target=self.llm.stop, name="llm-stop", daemon=True).start()

    def _on_config_changed(self, event: Event) -> None:
        keys = event.data.get("keys", [])
        if any(k.startswith("hotkeys.") or k == "voice.push_to_talk_mode" for k in keys):
            self._hotkeys_changed.emit()
        if "performance.game_mode_auto" in keys and self.game_watcher is not None:
            if self.ctx.config.data.performance.game_mode_auto:
                self.game_watcher.start()
            else:
                self.game_watcher.stop()

    def start(self) -> None:
        self.tray.show()
        self.popup.create()
        self._register_hotkeys()
        self.ctx.apply_autostart()
        for w in self.ctx.config.warnings:
            self.ctx.bus.publish(Topic.NOTIFY, title="Settings problem", message=w, level="warning")
        log.info("tray ready (system tray available: %s)", self.tray.available)
        # Heavy init (tool system, model) after the tray is visible; delayed when launched at login.
        first_run = not self.ctx.config.data.general.onboarding_done
        delay = self.ctx.config.data.general.startup_delay_s if self.autostarted and not first_run else 0.0
        QTimer.singleShot(int(delay * 1000), self._init_assistant)

    def _init_assistant(self) -> None:
        from ..core.assistant import Assistant
        from ..llm.service import LlmService
        from ..tools.runtime import build_tool_system
        from .confirm import GuiConfirmer, PopupConfirmer

        dialog = GuiConfirmer(self.ctx.config.data.general.assistant_name)
        self.confirmer = PopupConfirmer(self.popup if self.popup.view is not None else None, dialog)
        self.tools = build_tool_system(self.ctx, confirmer=self.confirmer)
        self.llm = LlmService(self.ctx)
        self.assistant = Assistant(self.ctx, self.tools, self.llm)
        self._start_game_watcher()
        if not self.ctx.config.data.general.onboarding_done or self.force_setup:
            log.info("assistant ready; opening the setup wizard")
            self.open_setup(first_run=not self.ctx.config.data.general.onboarding_done)
            return
        self.llm.start_background()
        log.info("assistant ready (LLM loading in the background)")
        threading.Thread(target=self._init_voice, name="voice-init", daemon=True).start()
        self._resume_downloads()

    def _start_game_watcher(self) -> None:
        from ..core.gamemode import GameModeWatcher

        if sys.platform != "win32" or self.game_watcher is not None:
            return
        self.game_watcher = GameModeWatcher(self.ctx)
        if self.ctx.config.data.performance.game_mode_auto:
            self.game_watcher.start()

    # ------------------------------------------------------------------ setup wizard + downloads
    def open_setup(self, first_run: bool = False) -> None:
        """The setup wizard (first start, or again from the tray menu). Models download meanwhile."""
        from ..onboarding.downloads import SetupManager
        from ..onboarding.env import WizardEnv
        from .onboarding import SetupWindow

        if self.tools is None:
            self.tray.notify("", "Still starting up - try again in a moment.")
            return
        if self.setup_window is not None:
            self.setup_window.show()
            return
        apps = self.tools.services["apps"]
        env = WizardEnv(ctx=self.ctx, policy=self.tools.policy, routines=self.tools.services["routines"],
                        app_names=lambda: [a.name for a in apps.apps()], first_run=first_run,
                        policy_changed=self.tools.reload_policy)

        def warm_up() -> None:      # app list + GPU info are needed a few questions later
            from ..core import hardware

            try:
                apps.apps()
                env.vram_mb = max((g.vram_total_mb for g in hardware.nvidia_gpus()), default=0)
            except Exception as e:
                log.debug("setup warm-up: %s", e)

        threading.Thread(target=warm_up, name="setup-warmup", daemon=True).start()
        if self.downloads is None:
            self.downloads = SetupManager(self.ctx)
        self.downloads.start()
        if self.voice is not None:   # re-run: the wizard uses the microphone itself
            threading.Thread(target=self._set_voice_paused, args=(True,), name="voice-pause", daemon=True).start()
        self.setup_window = SetupWindow(self.ctx, env, self.downloads, suspend_hotkeys=self._suspend_hotkeys)
        self.setup_window.completed.connect(self._setup_done)
        if not self.setup_window.show():
            log.error("setup wizard could not be shown; continuing with the current settings")
            self.setup_window = None
            self._setup_done()

    def _setup_done(self) -> None:
        from ..core.proc import trim_memory

        self.setup_window = None
        QTimer.singleShot(500, trim_memory)
        self.force_setup = False
        name = self.ctx.config.data.general.assistant_name
        if self.voice is None:
            threading.Thread(target=self._init_voice, name="voice-init", daemon=True).start()
        elif not self.ctx.state.paused:
            threading.Thread(target=self._set_voice_paused, args=(False,), name="voice-resume", daemon=True).start()
        self._maybe_start_llm()
        self.tray.refresh()
        self.ctx.bus.publish(Topic.NOTIFY, title=name, message=(
            f"Bereit - sag „{name}“, wenn du etwas brauchst." if self.ctx.config.data.general.language == "de"
            else f"Ready - just say “{name}” when you need me."))

    def _suspend_hotkeys(self, on: bool) -> None:
        """The wizard's hotkey page needs the keys itself while you press the new combination."""
        if on:
            self.hotkeys.stop()
        else:
            self._register_hotkeys()

    def _resume_downloads(self) -> None:
        """Downloads interrupted last time (app closed during setup) continue quietly."""
        from ..onboarding.downloads import SetupManager, human_size

        if self.downloads is None:
            self.downloads = SetupManager(self.ctx)
        missing = self.downloads.missing()
        if missing and self.downloads.start():
            size = human_size(sum(j.size for j in missing))
            self.ctx.bus.publish(Topic.NOTIFY, title="Downloads", message=f"Continuing model downloads ({size}).")

    def _llm_installed(self) -> bool:
        from ..llm.models import CATALOG, is_installed
        from ..llm.setup import current_server

        if not (self.ctx.config.data.llm.server_path or current_server(self.ctx.paths.bin_dir)):
            return False
        return any(is_installed(s, self.ctx.paths.models_dir, vision=False) for s in CATALOG.values() if not s.hidden)

    def _maybe_start_llm(self) -> None:
        """Load the language model as soon as llama.cpp and a model are on disk (also while the wizard runs)."""
        if self.llm is None or self.llm.ready or self.ctx.state.game_mode or not self._llm_installed():
            return
        if self.llm.server is None:
            self.llm.spec = None        # pick again: the preferred model may have arrived meanwhile
        self.llm.start_background()

    def _on_setup_event(self, event: Event) -> None:
        if event.topic == "setup.job_done" and event.data.get("kind") in ("llm", "llama"):
            self._maybe_start_llm()
        elif event.topic == "setup.finished":
            if self.setup_window is None:
                self._maybe_start_llm()
                if self.voice is None and self.ctx.config.data.general.onboarding_done:
                    threading.Thread(target=self._init_voice, name="voice-init", daemon=True).start()
            if event.data.get("failed") or event.data.get("error"):
                self.ctx.bus.publish(Topic.NOTIFY, title="Downloads", level="warning", message=(
                    event.data.get("error") or "Some downloads failed. They are retried at the next start "
                    "(or open the setup wizard from the tray menu)."))

    # ------------------------------------------------------------------ voice
    def _init_voice(self) -> None:
        """Load the wake word / VAD models (worker thread, ~0.5 s) and open the microphone."""
        from ..voice.runtime import VoiceUnavailable, build_voice

        if self.assistant is None:
            return
        t0 = time.perf_counter()
        try:
            stack = build_voice(self.ctx, self.assistant)
        except VoiceUnavailable as e:
            log.warning("voice off: %s", e)
            self.ctx.bus.publish(Topic.NOTIFY, title="Voice is not set up yet", level="warning",
                                 message="The speech models are missing - run scripts\\setup-models.bat. "
                                         "Until then you can type: tray icon → Type a request.")
            return
        except Exception as e:
            log.exception("voice could not be started")
            self.ctx.bus.publish(Topic.NOTIFY, title="Voice problem", message=str(e)[:240], level="warning")
            return
        self.voice = stack
        self.tray.beep_alarms = False       # the voice stack plays a proper alarm sound
        if self.confirmer is not None:
            self.confirmer.voice = stack.pipeline
        log.debug("TIMING stage=voice_init ms=%.1f", (time.perf_counter() - t0) * 1000)
        if not self.ctx.state.paused:
            self._set_voice_paused(False, retries=3)

    def _set_voice_paused(self, paused: bool, retries: int = 1) -> None:
        if self.voice is None:
            return
        for attempt in range(retries):
            try:
                self.voice.pipeline.set_paused(paused)
                return
            except Exception as e:   # no microphone, device busy, audio service not up yet (login)
                log.warning("microphone could not be opened: %s", e)
                if attempt + 1 < retries:
                    time.sleep(5)
                    continue
                self.ctx.bus.publish(Topic.NOTIFY, title="Microphone", level="warning",
                                     message=f"Could not open the microphone ({e}). Check Windows sound settings, "
                                             "then pause and resume listening in the tray menu.")

    def _register_hotkeys(self) -> None:
        hk = self.ctx.config.data.hotkeys
        hold = self.ctx.config.data.voice.push_to_talk_mode == "hold"
        bindings = []
        if hk.push_to_talk:
            bindings.append(Binding("push_to_talk", hk.push_to_talk, lambda: self._ptt(True),
                                    (lambda: self._ptt(False)) if hold else None))
        if hk.panic:
            bindings.append(Binding("panic", hk.panic, self.panic))
        errors = self.hotkeys.start(bindings)
        if not self.hotkeys.supported:
            log.info("global hotkeys are only available on Windows")
            return
        for name, err in errors.items():
            self.ctx.bus.publish(Topic.NOTIFY, title="Hotkey not available", level="warning",
                                 message=f"{name.replace('_', '-')}: {err}")

    def _ptt(self, down: bool) -> None:
        """Push-to-talk hotkey (hotkey thread). Without voice it opens the chat box instead."""
        v = self.voice
        hold = self.ctx.config.data.voice.push_to_talk_mode == "hold"
        if v is None or self.ctx.state.paused or not v.pipeline.running:
            if down:
                self._open_chat.emit()
            return
        if down:
            v.pipeline.ptt_press(hold=hold)
        else:
            v.pipeline.ptt_release()

    def panic(self) -> None:
        """Panic hotkey: stop talking, cancel everything, release mouse + keyboard."""
        log.info("panic hotkey")
        if self.voice is not None:
            self.voice.pipeline.cancel("panic")
        elif self.assistant is not None:
            self.assistant.cancel("panic")
        if self.tools is not None:
            try:
                self.tools.platform.input.release_all()
            except Exception:
                pass

    def _on_notify(self, event: Event) -> None:
        """Timers/reminders: a sound, and the label spoken when idle."""
        if event.data.get("level") != "alarm" or self.voice is None:
            return
        threading.Thread(target=self._alarm, args=(event.data.get("message", ""),), name="alarm",
                         daemon=True).start()

    def _alarm(self, message: str) -> None:
        from ..audio.earcons import sound

        v = self.voice
        try:
            v.speaker.player.play(sound("alarm"))
            if message and self.ctx.config.data.voice.tts_enabled and v.pipeline.state.value in ("idle", "off"):
                from ..core.cancel import CancelToken

                c = self.ctx.config.data
                lang = c.general.language
                v.speaker.wait(timeout=5)
                v.speaker.say(message, lang, c.voice.tts_voice_de if lang == "de" else c.voice.tts_voice_en,
                              c.voice.tts_speed, CancelToken())
        except Exception as e:
            log.debug("alarm sound failed: %s", e)

    # ------------------------------------------------------------------ typed requests
    def ask_dialog(self) -> None:
        """Tray "Type a request…" / push-to-talk without voice: the popup's mini chat."""
        if self.popup.view is not None:
            self.popup.open_chat()
            return
        from PySide6.QtWidgets import QInputDialog

        if self.assistant is None:
            self.tray.notify("", "Still starting up - try again in a moment.")
            return
        name = self.ctx.config.data.general.assistant_name
        text, ok = QInputDialog.getText(None, name, f"What should {name} do?")
        if ok and text.strip():
            threading.Thread(target=self._run_request, args=(text, True), name="request", daemon=True).start()

    def _submit(self, text: str) -> None:
        if self.assistant is None:
            self.ctx.bus.publish("assistant.reply", text="Still starting up - try again in a moment.", ok=False,
                                 via="error", intent="")
            return
        threading.Thread(target=self._run_request, args=(text, False), name="request", daemon=True).start()

    def _run_request(self, text: str, balloon: bool) -> None:
        assert self.assistant is not None
        reply = self.assistant.handle(text, source="typed")
        if balloon:
            self._answer.emit(reply.text, reply.ok)

    def _show_answer(self, text: str, ok: bool) -> None:
        self.tray.notify(self.ctx.config.data.general.assistant_name, text, "info" if ok else "warning")

    # ------------------------------------------------------------------ handlers
    def on_message(self, msg: str) -> None:
        log.info("message from second instance: %s", msg)
        if msg in ("show", "settings"):
            self.open_settings()
        elif msg == "quit":                 # --quit (the installer before an update, the uninstaller)
            self.quit()

    def open_settings(self, tab: str = "") -> None:
        """The settings window: created on demand, freed again when it is closed."""
        from .settings import SettingsDeps, SettingsWindow

        if self.tools is None:
            self.tray.notify("", "Still starting up - try again in a moment.")
            return
        if self.settings_window is not None:
            self.settings_window.show(tab)
            return
        deps = SettingsDeps(ctx=self.ctx, tools=self.tools, assistant=self.assistant, llm=self.llm,
                            voice=lambda: self.voice, popup=self.popup if self.popup.view is not None else None,
                            downloads=self._download_manager, open_setup=lambda: self.open_setup(first_run=False),
                            pause_voice=self._settings_pause_voice, suspend_hotkeys=self._suspend_hotkeys)
        self.settings_window = SettingsWindow(deps)
        self.settings_window.closed.connect(self._settings_closed)
        if not self.settings_window.show(tab):
            self.settings_window = None
            open_path(self.ctx.paths.config_file, edit=True)    # fallback: the file itself

    def _settings_closed(self) -> None:
        from ..core.proc import trim_memory

        self.settings_window = None
        QTimer.singleShot(500, trim_memory)      # after Qt has deleted the window and its QML engine
        log.info("settings window closed and freed")

    def _download_manager(self) -> Any:
        from ..onboarding.downloads import SetupManager

        if self.downloads is None:
            self.downloads = SetupManager(self.ctx)
        return self.downloads

    def _settings_pause_voice(self, on: bool) -> None:
        """Calibration / voice-lock recording in the settings: the wake word must not hear the takes."""
        if self.voice is None:
            return
        if on:
            threading.Thread(target=self.voice.pipeline.set_paused, args=(True,), daemon=True).start()
        elif not self.ctx.state.paused:
            threading.Thread(target=self._set_voice_paused, args=(False,), daemon=True).start()

    def _on_config_file_changed(self, path: str) -> None:
        self._reload_timer.start()

    def _reload_config(self) -> None:
        f = str(self.ctx.paths.config_file)
        if f not in self.watcher.files() and self.ctx.paths.config_file.exists():
            self.watcher.addPath(f)
        if self.ctx.config.reload_if_changed():
            for w in self.ctx.config.warnings:
                self.ctx.bus.publish(Topic.NOTIFY, title="Settings problem", message=w, level="warning")
            apply_theme(self.app, self.ctx.config.data.appearance.theme)

    def quit(self) -> None:
        log.info("quit requested")
        self.ctx.bus.publish(Topic.SHUTDOWN)
        if self.downloads is not None:
            self.downloads.cancel()
        if self.game_watcher is not None:
            self.game_watcher.close()
        if self.setup_window is not None:
            self.setup_window.voice.close()
        if self.settings_window is not None:
            self.settings_window.close()
        self.hotkeys.stop()
        if self.voice is not None:
            self.voice.pipeline.stop()
            self.voice.speaker.stop()
        if self.assistant is not None:
            self.assistant.cancel("quit")
        if self.llm is not None:
            self.llm.stop()
        if self.tools is not None:
            self.tools.stop()
        self.popup.close()
        self.tray.close()
        self.app.quit()


def run_tray(*, home: str | None = None, autostarted: bool = False, debug: bool = False,
             smoke_test_s: float | None = None, open_setup: bool = False, self_test: bool = False) -> int:
    temp_home = None
    if self_test:
        os.environ["ASSISTANT_NO_DOWNLOADS"] = "1"          # a test never downloads anything
        if home is None:
            temp_home = home = tempfile.mkdtemp(prefix="assistant-selftest-")
    set_app_user_model_id(APP_USER_MODEL_ID)
    ctx = bootstrap("tray", home=home, console_log=debug, console_level="DEBUG" if debug else "INFO")
    if self_test:
        from ..core.autostart import Autostart, MemoryBackend

        ctx.autostart = Autostart(backend=MemoryBackend())  # ... and never touches the login items
    qInstallMessageHandler(_qt_message)

    QApplication.setApplicationName(APP_ID)
    QApplication.setApplicationVersion(__version__)
    app = QApplication(sys.argv[:1])
    app.setQuitOnLastWindowClosed(False)  # tray app: closing a window must not quit
    apply_theme(app, ctx.config.data.appearance.theme)

    instance = SingleInstance(ctx.paths.lock_file)
    if not instance.acquire():
        delivered = instance.send("show")
        log.info("another instance is running; %s", "asked it to show settings" if delivered else "it did not answer")
        return 0

    tray_app = TrayApp(ctx, app, autostarted=autostarted, open_setup=open_setup)
    instance.message_received.connect(tray_app.on_message)
    tray_app.start()

    # Let Ctrl+C work when started from a console (Python only sees signals between Qt events).
    # Without a console (pythonw / the .exe) there is no Ctrl+C, so no need to wake up 4x a second.
    signal.signal(signal.SIGINT, lambda *_: tray_app.quit())
    pulse = QTimer(interval=250)
    pulse.timeout.connect(lambda: None)
    if sys.stdin is not None and sys.stdin.isatty():
        pulse.start()

    if smoke_test_s is not None:
        QTimer.singleShot(int(smoke_test_s * 1000), tray_app.quit)
        log.info("smoke test: quitting in %.1fs", smoke_test_s)
    test = None
    if self_test:
        from .selftest import SelfTest

        def out(line: str) -> None:
            print(line, flush=True)
            log.info("self-test: %s", line.strip())

        test = SelfTest(tray_app, out=out)
        test.start()
        QTimer.singleShot(180_000, lambda: test.abort("timed out after 3 minutes"))

    code = app.exec()
    instance.release()
    if test is not None:
        code = test.result
    log.info("exit code %d", code)
    if temp_home is not None:
        logging.shutdown()                                  # close the log file before deleting it
        shutil.rmtree(temp_home, ignore_errors=True)
    return code
