"""Tray application bootstrap (the default entry point)."""

from __future__ import annotations

import logging
import signal
import sys
from typing import Any

import threading

from PySide6.QtCore import QFileSystemWatcher, QObject, Qt, QTimer, QtMsgType, Signal, qInstallMessageHandler
from PySide6.QtWidgets import QApplication

from .. import APP_ID, APP_USER_MODEL_ID, __version__
from ..core.context import AppContext, bootstrap
from ..core.events import Topic
from ..win.shell import open_path, set_app_user_model_id
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
        self.tray.ask_requested.connect(self.ask_dialog)
        self._answer.connect(self._show_answer)
        ctx.bus.subscribe(Topic.STATE_CHANGED, self._on_state_changed)

    def _on_state_changed(self, event) -> None:  # type: ignore[no-untyped-def]
        """Game mode frees the GPU: stop the model now, reload when game mode ends."""
        changed = event.data.get("changed", {})
        if "game_mode" not in changed or self.llm is None:
            return
        llm = self.llm
        if changed["game_mode"]:
            threading.Thread(target=llm.stop, name="llm-stop", daemon=True).start()
            self.ctx.bus.publish(Topic.NOTIFY, title="Game mode", message="Language model unloaded - VRAM is free.")
        else:
            llm.start_background()

    def start(self) -> None:
        self.tray.show()
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
        from .confirm import GuiConfirmer

        self.confirmer = GuiConfirmer(self.ctx.config.data.general.assistant_name)
        self.tools = build_tool_system(self.ctx, confirmer=self.confirmer)
        self.llm = LlmService(self.ctx)
        self.assistant = Assistant(self.ctx, self.tools, self.llm)
        self.llm.start_background()
        log.info("assistant ready (LLM loading in the background)")

    # ------------------------------------------------------------------ typed requests (until the popup, M4)
    def ask_dialog(self) -> None:
        from PySide6.QtWidgets import QInputDialog

        if self.assistant is None:
            self.tray.notify("", "Still starting up - try again in a moment.")
            return
        name = self.ctx.config.data.general.assistant_name
        text, ok = QInputDialog.getText(None, name, f"What should {name} do?")
        if ok and text.strip():
            threading.Thread(target=self._run_request, args=(text,), name="request", daemon=True).start()

    def _run_request(self, text: str) -> None:
        assert self.assistant is not None
        reply = self.assistant.handle(text, source="typed")
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
        if self.assistant is not None:
            self.assistant.cancel("quit")
        if self.llm is not None:
            self.llm.stop()
        if self.tools is not None:
            self.tools.stop()
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
