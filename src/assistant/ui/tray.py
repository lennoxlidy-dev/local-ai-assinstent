"""System tray icon + menu."""

from __future__ import annotations

import logging
from collections.abc import Callable

from PySide6.QtCore import QObject, Signal, Slot
from PySide6.QtGui import QAction
from PySide6.QtWidgets import QMenu, QSystemTrayIcon

from ..core.autostart import AutostartState
from ..core.context import AppContext
from ..core.events import Event, Topic
from ..win.shell import open_path
from .icons import orb_icon

log = logging.getLogger(__name__)


class TrayController(QObject):
    """Owns the tray icon. Core events arrive on worker threads and are marshalled
    onto the GUI thread through Qt signals (``_bus_event``)."""

    _bus_event = Signal(object)
    quit_requested = Signal()
    settings_requested = Signal()
    ask_requested = Signal()

    def __init__(self, ctx: AppContext, parent: QObject | None = None):
        super().__init__(parent)
        self.ctx = ctx
        self.available = QSystemTrayIcon.isSystemTrayAvailable()
        self.tray = QSystemTrayIcon(self)
        self.menu = QMenu()
        self._build_menu()
        self.tray.setContextMenu(self.menu)
        self.tray.activated.connect(self._on_activated)
        self.menu.aboutToShow.connect(self._refresh_menu)

        self._bus_event.connect(self._on_bus_event)
        self._unsubs: list[Callable[[], None]] = [
            ctx.bus.subscribe(Topic.STATE_CHANGED, self._bus_event.emit),
            ctx.bus.subscribe(Topic.CONFIG_CHANGED, self._bus_event.emit),
            ctx.bus.subscribe(Topic.NOTIFY, self._bus_event.emit),
            ctx.bus.subscribe("llm.state", self._bus_event.emit),
        ]
        self.llm_state = ""
        self.beep_alarms = True
        self.refresh()

    # ------------------------------------------------------------------ menu
    def _build_menu(self) -> None:
        m = self.menu
        self.act_header = QAction("", m)
        self.act_header.setEnabled(False)
        m.addAction(self.act_header)
        m.addSeparator()

        self.act_ask = QAction("Type a request…", m)
        self.act_ask.triggered.connect(self.ask_requested.emit)
        m.addAction(self.act_ask)

        self.act_settings = QAction("Settings…", m)
        self.act_settings.triggered.connect(self.settings_requested.emit)
        m.addAction(self.act_settings)

        self.act_pause = QAction("Pause listening", m, checkable=True)
        self.act_pause.toggled.connect(lambda on: self.ctx.state.update(paused=on))
        m.addAction(self.act_pause)

        self.act_game = QAction("Game mode", m, checkable=True)
        self.act_game.toggled.connect(lambda on: self.ctx.state.update(game_mode=on))
        m.addAction(self.act_game)

        self.act_autostart = QAction("Start at login", m, checkable=True)
        self.act_autostart.triggered.connect(self._toggle_autostart)
        m.addAction(self.act_autostart)

        m.addSeparator()
        folder = m.addMenu("Open folder")
        folder.addAction("Settings && data", lambda: self._open(self.ctx.paths.config_dir))
        folder.addAction("Logs", lambda: self._open(self.ctx.paths.logs_dir))
        folder.addAction("Models", lambda: self._open(self.ctx.paths.models_dir))

        m.addSeparator()
        act_quit = QAction("Quit", m)
        act_quit.triggered.connect(self.quit_requested.emit)
        m.addAction(act_quit)

    def _refresh_menu(self) -> None:
        self.ctx.config.reload_if_changed()
        st = self.ctx.autostart.state()
        self.act_autostart.setEnabled(st is not AutostartState.UNSUPPORTED)
        self.act_autostart.setChecked(st is AutostartState.ENABLED)
        self.act_autostart.setText("Start at login" + (" (disabled by Windows)"
                                                       if st is AutostartState.DISABLED_BY_WINDOWS else ""))
        self.refresh()

    # ------------------------------------------------------------------ state
    def status_text(self) -> str:
        s, name = self.ctx.state, self.ctx.config.data.general.assistant_name
        if s.paused:
            return f"{name} — paused"
        if s.game_mode:
            return f"{name} — game mode"
        extra = f" ({self.llm_state})" if getattr(self, "llm_state", "") else ""
        if s.voice_ready:
            return f"{name} — listening for “{name}”{extra}"
        return f"{name} — ready{extra}"

    def refresh(self) -> None:
        s = self.ctx.state
        accent = self.ctx.config.data.appearance.accent
        self.tray.setIcon(orb_icon(accent, paused=s.paused, game_mode=s.game_mode,
                                   listening=s.phase in ("listening",)))
        text = self.status_text()
        self.tray.setToolTip(text)
        self.act_header.setText(text)
        for act, val in ((self.act_pause, s.paused), (self.act_game, s.game_mode)):
            if act.isChecked() != val:
                act.blockSignals(True)
                act.setChecked(val)
                act.blockSignals(False)

    @Slot(object)
    def _on_bus_event(self, event: Event) -> None:
        if event.topic == "llm.state":
            self.llm_state = {"starting": "model loading…", "ready": "", "failed": "model unavailable",
                              "stopped": "model unloaded"}.get(event.data.get("state", ""), "")
            self.refresh()
            return
        if event.topic == Topic.NOTIFY:
            self.notify(event.data.get("title", ""), event.data.get("message", ""),
                        event.data.get("level", "info"))
            return
        if event.topic == Topic.CONFIG_CHANGED and "general.autostart" in event.data.get("keys", []):
            self.ctx.apply_autostart()
        self.refresh()

    # ------------------------------------------------------------------ actions
    def _toggle_autostart(self, on: bool) -> None:
        if on:
            self.ctx.autostart.enable()  # explicit: also clears a "disabled by Windows" flag
        self.ctx.config.set("general.autostart", on)
        if not on:
            self.ctx.autostart.disable()
        self._refresh_menu()

    def _on_activated(self, reason: QSystemTrayIcon.ActivationReason) -> None:
        if reason == QSystemTrayIcon.ActivationReason.DoubleClick:
            self.settings_requested.emit()

    def _open(self, path) -> None:  # type: ignore[no-untyped-def]
        try:
            open_path(path)
        except OSError as e:
            self.notify("Could not open folder", str(e), "warning")

    def notify(self, title: str, message: str, level: str = "info") -> None:
        if level == "alarm" and self.beep_alarms:  # timers/reminders: make them noticeable
            from PySide6.QtWidgets import QApplication

            QApplication.beep()
        icon = {"warning": QSystemTrayIcon.MessageIcon.Warning,
                "error": QSystemTrayIcon.MessageIcon.Critical}.get(level, QSystemTrayIcon.MessageIcon.Information)
        if self.available and self.tray.isVisible():
            self.tray.showMessage(title or self.ctx.config.data.general.assistant_name, message, icon, 5000)
        log.info("notify[%s] %s: %s", level, title, message)

    def show(self) -> None:
        if not self.available:
            log.warning("no system tray available; running without a tray icon")
            return
        self.tray.show()

    def close(self) -> None:
        for u in self._unsubs:
            u()
        self.tray.hide()
