"""One live colour palette for the QML windows (wizard, settings): ``look.colors`` in QML.

Follows ``appearance.theme`` (dark / light / system) and ``appearance.accent`` and updates
open windows immediately when they change.
"""

from __future__ import annotations

from typing import Any

from PySide6.QtCore import Qt, Signal

from ..core.context import AppContext
from ..core.events import Event, Topic
from .qtmodel import model_class

DARK = {"bg": "#16161A", "card": "#1F1F25", "card2": "#26262E", "field": "#1A1A20", "fg": "#F2F3F5",
        "dim": "#A3A7B0", "border": "#33343C", "warn": "#F5B44A", "ok": "#3DD68C", "danger": "#F2555A",
        "side": "#121216", "hover": "#2B2B34"}
LIGHT = {"bg": "#F4F5F8", "card": "#FFFFFF", "card2": "#ECEDF2", "field": "#FFFFFF", "fg": "#1B1C20",
         "dim": "#5E626B", "border": "#D9DBE2", "warn": "#B26B00", "ok": "#178A52", "danger": "#C9333A",
         "side": "#EBECF1", "hover": "#E2E4EA"}

LookBase: Any = model_class("LookBase", {"colors": ("QVariantMap", {}), "dark": (bool, True)})


def is_dark(theme: str) -> bool:
    if theme == "light":
        return False
    if theme == "dark":
        return True
    try:
        from PySide6.QtGui import QGuiApplication

        hints = QGuiApplication.styleHints()
        return not hasattr(hints, "colorScheme") or hints.colorScheme() != Qt.ColorScheme.Light
    except Exception:
        return True


def palette(ctx: AppContext) -> dict[str, Any]:
    a = ctx.config.data.appearance
    dark = is_dark(a.theme)
    colors: dict[str, Any] = dict(DARK if dark else LIGHT)
    colors["accent"] = a.accent
    from ..win.dwm import windows_build

    colors["font"] = "Segoe UI Variable Text" if windows_build() >= 22000 else "Segoe UI"
    colors["icons"] = "Segoe Fluent Icons" if windows_build() >= 22000 else "Segoe MDL2 Assets"
    colors["iconFont"] = windows_build() > 0
    return colors


class Look(LookBase):
    """The ``look`` context property. Thread-safe: config changes arrive via a queued signal."""

    _changed = Signal()

    def __init__(self, ctx: AppContext):
        super().__init__()
        self.ctx = ctx
        self._changed.connect(self.refresh, Qt.ConnectionType.QueuedConnection)
        self._unsub = ctx.bus.subscribe(Topic.CONFIG_CHANGED, self._on_config)
        self.refresh()

    def _on_config(self, e: Event) -> None:
        if any(k.startswith("appearance.") for k in e.data.get("keys", [])):
            self._changed.emit()

    def refresh(self) -> None:
        self.set(colors=palette(self.ctx), dark=is_dark(self.ctx.config.data.appearance.theme))

    def close(self) -> None:
        self._unsub()


__all__ = ["DARK", "LIGHT", "Look", "is_dark", "palette"]
