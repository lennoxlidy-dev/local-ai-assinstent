"""The popup: a small frameless, always-on-top window with the orb that never takes the focus.

Created hidden at startup, so it appears within a frame or two of the wake word.
Core events arrive on worker threads and are queued onto the GUI thread with a Qt
signal; the controller turns them into ``PopupModel`` properties that Popup.qml binds to.

Rendering uses Qt Quick's software backend: a window this small is cheap to draw on
the CPU, per-pixel transparency (rounded corners, fade) works everywhere, and the GPU
stays free for games and the language model.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from PySide6.QtCore import Property, QObject, QRect, Qt, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QColor, QCursor, QGuiApplication, QScreen, QSurfaceFormat

from ..core.context import AppContext
from ..core.events import Event, Topic
from ..permissions.gate import ConfirmChoice, ConfirmRequest

log = logging.getLogger(__name__)

QML_DIR = Path(__file__).with_name("qml")
MARGIN = 18
RADIUS = 14
MAX_MESSAGES = 80
SHORT_FADE_MS = 1800
_BACKEND_SET = False

TEXTS: dict[str, dict[str, str]] = {
    "en": {
        "listening": "Listening…", "thinking": "Thinking…", "acting": "Working…", "speaking": "Speaking",
        "done": "Done", "error": "Something went wrong", "follow_up": "Anything else?",
        "nothing": "Didn't catch that", "rejected": "Voice not recognized", "confirm": "Needs your OK",
        "placeholder": "Message {name}…", "empty": "Ask {name} anything.", "alarm": "Reminder",
        "hint_voice": "Say “yes” or “no” – or click.", "once": "Allow once", "task": "Allow for this task",
        "always": "Always allow “{cap}”", "deny": "Don't allow", "loading model": "Loading the language model…",
        "loading speech recognition": "Loading speech recognition…", "refused": "Not allowed: {what}",
        "asking": "Waiting for your OK: {what}",
    },
    "de": {
        "listening": "Ich höre…", "thinking": "Denke nach…", "acting": "Bin dran…", "speaking": "Spreche",
        "done": "Erledigt", "error": "Etwas ist schiefgelaufen", "follow_up": "Noch etwas?",
        "nothing": "Nichts verstanden", "rejected": "Stimme nicht erkannt", "confirm": "Bitte bestätigen",
        "placeholder": "Nachricht an {name}…", "empty": "Frag {name} etwas.", "alarm": "Erinnerung",
        "hint_voice": "Sag „ja“ oder „nein“ – oder klick.", "once": "Einmal erlauben",
        "task": "Für diese Aufgabe erlauben", "always": "„{cap}“ immer erlauben", "deny": "Nicht erlauben",
        "loading model": "Sprachmodell wird geladen…", "loading speech recognition": "Spracherkennung wird geladen…",
        "refused": "Nicht erlaubt: {what}", "asking": "Warte auf deine Bestätigung: {what}",
    },
}

# property name -> (Qt type, default)
_FIELDS: dict[str, tuple[Any, Any]] = {
    "visibleState": (bool, False), "expanded": (bool, False), "dark": (bool, True), "blur": (bool, False),
    "radius": (int, RADIUS), "accent": (str, "#7C5CFF"), "state": (str, "idle"), "level": (float, 0.0),
    "statusText": (str, ""), "transcript": (str, ""), "progress": (str, ""), "answer": (str, ""),
    "title": (str, "Assistant"), "placeholder": (str, ""), "emptyText": (str, ""),
    "messages": ("QVariantList", []), "confirmActive": (bool, False), "confirmTitle": (str, ""),
    "confirmDetails": (str, ""), "confirmRisk": (str, ""), "confirmHint": (str, ""),
    "confirmOptions": ("QVariantList", []),
}


def _model_base() -> type:
    """A QObject class with one notifying, read-only Qt property per field (the QML binds to them)."""
    ns: dict[str, Any] = {}
    for name, (typ, _default) in _FIELDS.items():
        sig = Signal()
        ns[f"{name}Changed"] = sig
        ns[name] = Property(typ, (lambda n: lambda self: self._values[n])(name), notify=sig)
    return type("_PopupModelBase", (QObject,), ns)


class PopupModel(_model_base()):  # type: ignore[misc]
    """The QML side's ``popup`` object. Only touched on the GUI thread."""

    focusRequested = Signal()
    expandRequested = Signal()
    collapseRequested = Signal()
    submitted = Signal(str)
    chosen = Signal(str)
    hovered = Signal(bool)
    resized = Signal(int, int)

    def __init__(self) -> None:
        super().__init__()
        self._values: dict[str, Any] = {k: (list(v) if isinstance(v, list) else v) for k, (_, v) in _FIELDS.items()}

    def get(self, name: str) -> Any:
        return self._values[name]

    def set(self, **values: Any) -> None:
        for k, v in values.items():
            if k not in self._values:
                raise AttributeError(k)
            if self._values[k] != v or isinstance(v, list):
                self._values[k] = v
                getattr(self, f"{k}Changed").emit()

    # called from QML
    @Slot()
    def expand(self) -> None:
        self.expandRequested.emit()

    @Slot()
    def collapse(self) -> None:
        self.collapseRequested.emit()

    @Slot(str)
    def submit(self, text: str) -> None:
        self.submitted.emit(text)

    @Slot(str)
    def choose(self, choice: str) -> None:
        self.chosen.emit(choice)

    @Slot(bool)
    def hover(self, on: bool) -> None:
        self.hovered.emit(on)

    @Slot(int, int)
    def resizeTo(self, w: int, h: int) -> None:  # noqa: N802 - Qt naming
        self.resized.emit(w, h)


@dataclass
class PendingConfirm:
    """A permission question waiting for a click or a spoken yes/no (whichever comes first)."""

    request: ConfirmRequest
    voice: bool = False
    done: threading.Event = field(default_factory=threading.Event)
    choice: ConfirmChoice = ConfirmChoice.DENY
    _lock: threading.Lock = field(default_factory=threading.Lock)

    def resolve(self, choice: ConfirmChoice) -> bool:
        with self._lock:
            if self.done.is_set():
                return False
            self.choice = choice
            self.done.set()
            return True


class PopupController(QObject):
    _bus_event = Signal(object)
    _show_confirm = Signal(object)
    _close_confirm = Signal(object)

    TOPICS = ("voice.state", "voice.level", "voice.partial", "voice.transcript", "voice.status",
              "voice.nothing_heard", "voice.false_trigger", "voice.rejected", "assistant.request",
              "assistant.status", "assistant.reply", "tool.start", "tool.refused", Topic.CONFIG_CHANGED,
              Topic.NOTIFY)

    def __init__(self, ctx: AppContext, *, on_submit: Callable[[str], None] | None = None):
        super().__init__()
        self.ctx = ctx
        self.on_submit = on_submit
        self.model = PopupModel()
        self.view: Any = None          # QQuickView once create() ran
        self._size = (420, 84)
        self._screen: QScreen | None = None
        self._hovered = False
        self._voice_state = "off"
        self._busy = False
        self._acting = False
        self._result = ""              # done | error | nothing | rejected | alarm
        self._pending: PendingConfirm | None = None
        self._wake_ts: float | None = None
        self._blur = False
        self._short_fade: int | None = None   # quicker fade after "didn't catch that" / false triggers

        self._fade = QTimer(self, singleShot=True)
        self._fade.timeout.connect(self.fade_out)
        self._hide_timer = QTimer(self, singleShot=True, interval=440)
        self._hide_timer.timeout.connect(self._hide_now)

        m = self.model
        m.expandRequested.connect(self.expand)
        m.collapseRequested.connect(self.collapse)
        m.submitted.connect(self._on_submit)
        m.chosen.connect(self._on_choice)
        m.hovered.connect(self._on_hover)
        m.resized.connect(self._on_resized)
        self._bus_event.connect(self._on_event)
        self._show_confirm.connect(self._confirm_open)
        self._close_confirm.connect(self._confirm_close)
        self._unsubs = [ctx.bus.subscribe(t, self._bus_event.emit) for t in self.TOPICS]
        self.apply_appearance()

    # ------------------------------------------------------------------ window
    def create(self) -> bool:
        """Build the (hidden) window. Returns False if Qt Quick is unavailable - the app then runs without it."""
        try:
            from PySide6.QtQuick import QQuickView, QQuickWindow, QSGRendererInterface

            global _BACKEND_SET
            if not _BACKEND_SET:   # once per process, before the first Qt Quick window
                QQuickWindow.setGraphicsApi(QSGRendererInterface.GraphicsApi.Software)
                _BACKEND_SET = True
            view = QQuickView()
            fmt = QSurfaceFormat(view.format())
            fmt.setAlphaBufferSize(8)
            view.setFormat(fmt)
            view.setColor(QColor(0, 0, 0, 0))
            view.setFlags(self._flags(expanded=False))
            view.setProperty("_q_showWithoutActivating", True)
            view.setTitle(self.ctx.config.data.general.assistant_name)
            view.setResizeMode(QQuickView.ResizeMode.SizeRootObjectToView)
            view.rootContext().setContextProperty("popup", self.model)
            view.setSource(QUrl.fromLocalFile(str(QML_DIR / "Popup.qml")))
            if view.status() != QQuickView.Status.Ready:
                for err in view.errors():
                    log.error("popup QML: %s", err.toString())
                return False
            view.resize(*self._size)
            view.create()           # native window now, so the first show is instant
            view.frameSwapped.connect(self._on_frame)
            self.view = view
            self._style_native()
            log.info("popup ready")
            return True
        except Exception:
            log.exception("popup could not be created")
            return False

    def _flags(self, *, expanded: bool) -> Qt.WindowType:
        f = Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint \
            | Qt.WindowType.NoDropShadowWindowHint
        if not expanded:
            f |= Qt.WindowType.WindowDoesNotAcceptFocus
        return f

    def _hwnd(self) -> int:
        return int(self.view.winId()) if self.view is not None else 0

    def _style_native(self) -> None:
        """Acrylic + rounded clip + no-activate on Windows; plain translucent card elsewhere."""
        from ..win import dwm

        hwnd = self._hwnd()
        if not hwnd:
            return
        dwm.disable_system_corners(hwnd)
        dwm.set_no_activate(hwnd, not self.model.get("expanded"))
        want = self.ctx.config.data.appearance.popup_blur
        dark = self.model.get("dark")
        self._blur = dwm.set_acrylic(hwnd, want, 0x141418 if dark else 0xF4F4F8) if want or self._blur else False
        self.model.set(blur=self._blur)
        self._clip()

    def _clip(self) -> None:
        if self.view is None:
            return
        from ..win import dwm

        dpr = self.view.devicePixelRatio()
        w, h = self.view.width(), self.view.height()
        dwm.set_round_region(self._hwnd(), round(w * dpr), round(h * dpr),
                             round(RADIUS * dpr) if self._blur else 0)

    def _pick_screen(self) -> QScreen | None:
        return QGuiApplication.screenAt(QCursor.pos()) or QGuiApplication.primaryScreen()

    def geometry_for(self, w: int, h: int, screen: QScreen | None = None) -> QRect:
        screen = screen or self._screen or self._pick_screen()
        a = screen.availableGeometry() if screen is not None else QRect(0, 0, 1920, 1080)
        vert, horiz = (self.ctx.config.data.appearance.popup_position.split("-") + ["center"])[:2]
        x = {"left": a.left() + MARGIN, "right": a.right() - MARGIN - w + 1}.get(horiz, a.center().x() - w // 2 + 1)
        y = a.top() + MARGIN if vert == "top" else a.bottom() - MARGIN - h + 1
        return QRect(x, y, w, h)

    def _on_resized(self, w: int, h: int) -> None:
        self._size = (w, h)
        if self.view is not None and self.view.isVisible():
            self.view.setGeometry(self.geometry_for(w, h))
            self._clip()

    def show(self) -> None:
        if self.view is None:
            return
        self._hide_timer.stop()
        if not self.view.isVisible():
            self._screen = self._pick_screen()
            self.view.setGeometry(self.geometry_for(*self._size))
            self.view.show()
            self._clip()
        if QGuiApplication.platformName() != "offscreen":
            self.view.raise_()
        self.model.set(visibleState=True)

    def fade_out(self) -> None:
        if self._hovered or self.model.get("expanded") or self._pending is not None:
            return
        self.model.set(visibleState=False)
        self._hide_timer.start()

    def _hide_now(self) -> None:
        if self.view is not None and not self.model.get("visibleState"):
            self.view.hide()
            self._screen = None

    def _on_frame(self) -> None:
        if self._wake_ts is not None:
            log.debug("TIMING stage=wake_to_popup ms=%.1f", (time.time() - self._wake_ts) * 1000)
            self._wake_ts = None

    def demo(self, text: str) -> None:
        """Settings > Appearance: show what an answer looks like (fades like a real one)."""
        self._new_turn()
        self._result = "done"
        self.model.set(transcript="", answer=text, progress="")
        self._refresh()
        self.show()
        self._schedule_fade()

    # ------------------------------------------------------------------ mini chat
    def open_chat(self) -> None:
        self.show()
        self.expand()

    def expand(self) -> None:
        if self.model.get("expanded"):
            self.model.focusRequested.emit()
            return
        self._fade.stop()
        self.model.set(expanded=True, visibleState=True)
        if self.view is not None:
            self.view.setFlags(self._flags(expanded=True))
            from ..win import dwm

            dwm.set_no_activate(self._hwnd(), False)
            self.show()
            self.view.requestActivate()
        self.model.focusRequested.emit()

    def collapse(self) -> None:
        if not self.model.get("expanded"):
            return
        self.model.set(expanded=False)
        if self.view is not None:
            self.view.setFlags(self._flags(expanded=False))
            from ..win import dwm

            dwm.set_no_activate(self._hwnd(), True)
        self._hovered = False
        self.fade_out()

    def _on_submit(self, text: str) -> None:
        text = text.strip()
        if text and self.on_submit is not None:
            self.on_submit(text)

    def _on_hover(self, on: bool) -> None:
        self._hovered = on
        if on:
            self._fade.stop()
            if self.view is not None and self.view.isVisible() and not self.model.get("visibleState"):
                self.show()   # caught it while fading out
        else:
            self._schedule_fade()

    # ------------------------------------------------------------------ permission questions
    def ask(self, pending: PendingConfirm) -> None:
        """Thread-safe: show the question (any thread)."""
        self._show_confirm.emit(pending)

    def close_question(self, pending: PendingConfirm) -> None:
        """Thread-safe: remove the question once it is answered / timed out."""
        self._close_confirm.emit(pending)

    def _confirm_open(self, p: PendingConfirm) -> None:
        if p.done.is_set():
            return
        self._pending = p
        req, t = p.request, self.t
        options: list[dict[str, Any]] = [{"id": ConfirmChoice.ONCE.value, "label": t("once"), "primary": True}]
        if req.allow_task:
            options.append({"id": ConfirmChoice.TASK.value, "label": t("task")})
        if req.allow_always:
            options.append({"id": ConfirmChoice.ALWAYS.value, "label": t("always", cap=req.capability_title)})
        options.append({"id": ConfirmChoice.DENY.value, "label": t("deny")})
        details = [d for d in req.action.details if d] + [f"({r})" for r in req.reasons if r]
        self.model.set(confirmActive=True, confirmTitle=req.action.summary, confirmDetails="\n".join(details),
                       confirmRisk="; ".join(req.risk_titles), confirmHint=t("hint_voice") if p.voice else "",
                       confirmOptions=options)
        self._fade.stop()
        self._refresh()
        self.show()

    def _confirm_close(self, p: PendingConfirm) -> None:
        if self._pending is not p:
            return
        self._pending = None
        self.model.set(confirmActive=False, confirmTitle="", confirmDetails="", confirmRisk="", confirmHint="",
                       confirmOptions=[])
        self._refresh()
        self._schedule_fade()

    def _on_choice(self, choice: str) -> None:
        p = self._pending
        if p is None:
            return
        try:
            c = ConfirmChoice(choice)
        except ValueError:
            c = ConfirmChoice.DENY
        p.resolve(c)
        self._confirm_close(p)

    # ------------------------------------------------------------------ events -> model
    def t(self, key: str, **kw: Any) -> str:
        lang = self.ctx.config.data.general.language
        s = TEXTS.get(lang, TEXTS["en"]).get(key) or TEXTS["en"].get(key, key)
        return s.format(**kw) if kw else s

    def apply_appearance(self) -> None:
        c = self.ctx.config.data
        theme = c.appearance.theme
        dark = theme == "dark"
        if theme == "system":
            hints = QGuiApplication.styleHints() if QGuiApplication.instance() is not None else None
            dark = hints is None or not hasattr(hints, "colorScheme") or \
                hints.colorScheme() != Qt.ColorScheme.Light
        name = c.general.assistant_name
        self.model.set(dark=dark, accent=c.appearance.accent, title=name,
                       placeholder=self.t("placeholder", name=name), emptyText=self.t("empty", name=name))
        if self.view is not None:
            self.view.setTitle(name)
            self._style_native()
        self._refresh()

    def _new_turn(self) -> None:
        self._result = ""
        self._acting = False
        self._short_fade = None
        self.model.set(transcript="", answer="", progress="")

    def _append(self, role: str, text: str, ok: bool = True) -> None:
        msgs = list(self.model.get("messages"))[-(MAX_MESSAGES - 1):]
        msgs.append({"role": role, "text": text, "ok": ok})
        self.model.set(messages=msgs)

    def _refresh(self) -> None:
        """Derive the orb mode + status line from what is going on right now."""
        v, t = self._voice_state, self.t
        if self._pending is not None and v != "listening":
            mode, status = "confirm", t("confirm")
        elif v == "listening":
            mode, status = "listening", t("listening")
        elif v == "speaking":
            mode, status = "speaking", t("speaking")
        elif self._busy:
            mode, status = ("acting", t("acting")) if self._acting else ("thinking", t("thinking"))
        elif v in ("transcribing", "busy"):
            mode, status = "thinking", t("thinking")
        elif self._result:
            mode = "done" if self._result in ("done", "alarm") else "error"
            status = t(self._result)
            if v == "follow_up" and self._result == "done":
                status = t("follow_up")
        else:
            mode, status = "idle", ""
        self.model.set(state=mode, statusText=status)

    def _schedule_fade(self, ms: int | None = None) -> None:
        if self.model.get("expanded") or self._pending is not None or self._hovered or self._busy or \
                self._voice_state in ("listening", "transcribing", "busy", "speaking"):
            self._fade.stop()
            return
        if not self.model.get("visibleState"):
            return
        if ms is None:
            ms = self._short_fade or int(self.ctx.config.data.appearance.popup_timeout_s * 1000)
        self._fade.start(ms)

    @Slot(object)
    def _on_event(self, e: Event) -> None:
        topic, d, m = e.topic, e.data, self.model
        if topic == "voice.level":
            m.set(level=float(d.get("level", 0.0)))
            return
        if topic == "voice.state":
            st, kind = d.get("state", ""), d.get("kind", "")
            prev, self._voice_state = self._voice_state, st
            if st == "listening" and kind in ("wake", "ptt", "ptt_hold", "follow_up") and prev != "listening":
                self._new_turn()
                if kind != "follow_up":
                    self._wake_ts = e.ts
                self.show()
            if st not in ("listening", "speaking"):
                m.set(level=0.0)
        elif topic == "voice.partial":
            if not m.get("answer"):
                m.set(transcript=d.get("text", ""))
        elif topic == "voice.transcript":
            m.set(transcript=d.get("text", ""))
        elif topic == "voice.status":
            m.set(progress=self.t(d.get("text", "")))
        elif topic == "voice.nothing_heard":
            self._result, self._short_fade = "nothing", SHORT_FADE_MS
        elif topic == "voice.rejected":
            self._result, self._short_fade = "rejected", SHORT_FADE_MS
        elif topic == "voice.false_trigger":
            self._result, self._short_fade = "", 200    # not meant for us: get out of the way
        elif topic == "assistant.request":
            self._busy, self._acting, self._short_fade = True, False, None
            text = d.get("text", "")
            if d.get("source") != "voice":
                self._new_turn()
                m.set(transcript=text)
                self.show()
            self._append("user", text)
        elif topic == "assistant.status":
            m.set(progress=self.t(d.get("text", "")))
        elif topic == "tool.start":
            self._acting = True
            m.set(progress=d.get("summary", "") or d.get("name", ""))
        elif topic == "tool.refused":
            what = d.get("summary", "") or d.get("name", "")
            m.set(progress=self.t("refused", what=what))
        elif topic == "assistant.reply":
            self._busy = self._acting = False
            text, ok = d.get("text", ""), bool(d.get("ok", True))
            self._result = "done" if ok else "error"
            m.set(answer=text, progress="")
            if text:
                self._append("assistant", text, ok)
        elif topic == Topic.NOTIFY:
            if d.get("level") != "alarm":
                return
            self._new_turn()
            self._result = "alarm"
            m.set(transcript=d.get("title", ""), answer=d.get("message", "") or d.get("title", ""))
            self.show()
        elif topic == Topic.CONFIG_CHANGED:
            if any(k.startswith(("appearance.", "general.assistant_name", "general.language"))
                   for k in d.get("keys", [])):
                self.apply_appearance()
            return
        self._refresh()
        if m.get("visibleState"):
            self._schedule_fade()

    # ------------------------------------------------------------------ lifecycle
    def close(self) -> None:
        for u in self._unsubs:
            u()
        self._unsubs = []
        if self._pending is not None:
            self._pending.resolve(ConfirmChoice.DENY)
        if self.view is not None:
            self.view.hide()
            self.view.setSource(QUrl())      # unload the QML before the model goes away
            self.view.deleteLater()
            self.view = None
