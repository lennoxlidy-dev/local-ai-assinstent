"""Tray application bootstrap (the default entry point)."""

from __future__ import annotations

import logging
import signal
import sys
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

    def __init__(self, ctx: AppContext, app: QApplication, *, autostarted: bool = False):
        super().__init__()
        self.ctx = ctx
        self.app = app
        self.autostarted = autostarted
        self.tray = TrayController(ctx)
        self.tray.quit_requested.connect(self.quit)
        self.tray.settings_requested.connect(self.open_settings)

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
        self.popup = PopupController(ctx, on_submit=self._submit)
        self.hotkeys = HotkeyManager()
        self.tray.ask_requested.connect(self.ask_dialog)
        self._answer.connect(self._show_answer)
        self._open_chat.connect(self.ask_dialog)
        self._hotkeys_changed.connect(self._register_hotkeys)
        ctx.bus.subscribe(Topic.STATE_CHANGED, self._on_state_changed)
        ctx.bus.subscribe(Topic.CONFIG_CHANGED, self._on_config_changed)
        ctx.bus.subscribe(Topic.NOTIFY, self._on_notify)

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
            self.ctx.bus.publish(Topic.NOTIFY, title="Game mode", message="Language model unloaded - VRAM is free.")
        else:
            llm.start_background()

    def _on_config_changed(self, event: Event) -> None:
        keys = event.data.get("keys", [])
        if any(k.startswith("hotkeys.") or k == "voice.push_to_talk_mode" for k in keys):
            self._hotkeys_changed.emit()

    def start(self) -> None:
        self.tray.show()
        self.popup.create()
        self._register_hotkeys()
        self.ctx.apply_autostart()
        for w in self.ctx.config.warnings:
            self.ctx.bus.publish(Topic.NOTIFY, title="Settings problem", message=w, level="warning")
        log.info("tray ready (system tray available: %s)", self.tray.available)
        # Heavy init (tool system, model) after the tray is visible; delayed when launched at login.
        delay = self.ctx.config.data.general.startup_delay_s if self.autostarted else 0.0
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
        self.llm.start_background()
        log.info("assistant ready (LLM loading in the background)")
        threading.Thread(target=self._init_voice, name="voice-init", daemon=True).start()

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

    def open_settings(self) -> None:
        # The real settings window arrives in milestone 6; until then edit the file.
        open_path(self.ctx.paths.config_file, edit=True)

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
             smoke_test_s: float | None = None) -> int:
    set_app_user_model_id(APP_USER_MODEL_ID)
    ctx = bootstrap("tray", home=home, console_log=debug, console_level="DEBUG" if debug else "INFO")
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

    tray_app = TrayApp(ctx, app, autostarted=autostarted)
    instance.message_received.connect(tray_app.on_message)
    tray_app.start()

    # Let Ctrl+C work when started from a console (Python only sees signals between Qt events).
    signal.signal(signal.SIGINT, lambda *_: tray_app.quit())
    pulse = QTimer(interval=250)
    pulse.timeout.connect(lambda: None)
    pulse.start()

    if smoke_test_s is not None:
        QTimer.singleShot(int(smoke_test_s * 1000), tray_app.quit)
        log.info("smoke test: quitting in %.1fs", smoke_test_s)

    code = app.exec()
    instance.release()
    log.info("exit code %d", code)
    return code
