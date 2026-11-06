"""Global hotkeys (push-to-talk, panic) via RegisterHotKey on a thread with its own message loop.

RegisterHotKey needs no keyboard hook, so nothing watches your typing; Windows
just posts WM_HOTKEY for the registered combinations. Hold-to-talk polls the key
state only while the hotkey is held down.
"""

from __future__ import annotations

import ctypes
import logging
import sys
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass

log = logging.getLogger(__name__)

MOD_ALT, MOD_CONTROL, MOD_SHIFT, MOD_WIN, MOD_NOREPEAT = 0x1, 0x2, 0x4, 0x8, 0x4000
WM_HOTKEY, WM_QUIT = 0x0312, 0x0012

_MODS = {"ctrl": MOD_CONTROL, "control": MOD_CONTROL, "strg": MOD_CONTROL, "alt": MOD_ALT, "shift": MOD_SHIFT,
         "win": MOD_WIN, "super": MOD_WIN, "meta": MOD_WIN}
_KEYS = {"space": 0x20, "escape": 0x1B, "esc": 0x1B, "enter": 0x0D, "return": 0x0D, "tab": 0x09,
         "backspace": 0x08, "insert": 0x2D, "ins": 0x2D, "delete": 0x2E, "del": 0x2E, "home": 0x24, "end": 0x23,
         "pageup": 0x21, "pgup": 0x21, "pagedown": 0x22, "pgdn": 0x22, "up": 0x26, "down": 0x28, "left": 0x25,
         "right": 0x27, "pause": 0x13, "scrolllock": 0x91, "printscreen": 0x2C, "capslock": 0x14,
         "`": 0xC0, "backquote": 0xC0, "-": 0xBD, "=": 0xBB, "[": 0xDB, "]": 0xDD, ";": 0xBA, "'": 0xDE,
         ",": 0xBC, ".": 0xBE, "/": 0xBF, "\\": 0xDC, "^": 0xDC}
_KEYS.update({f"f{i}": 0x6F + i for i in range(1, 25)})
_KEYS.update({f"num{i}": 0x60 + i for i in range(10)})
_KEYS.update({f"numpad{i}": 0x60 + i for i in range(10)})


def parse_hotkey(spec: str) -> tuple[int, int]:
    """'ctrl+alt+space' -> (modifiers, virtual key). Raises ValueError for anything it can't map."""
    parts = [p.strip().lower() for p in spec.replace(" ", "").split("+") if p.strip()]
    if not parts:
        raise ValueError("empty hotkey")
    mods, key = 0, None
    for p in parts:
        if p in _MODS:
            mods |= _MODS[p]
        elif key is None:
            if p in _KEYS:
                key = _KEYS[p]
            elif len(p) == 1 and (p.isascii() and p.isalnum()):
                key = ord(p.upper())
            else:
                raise ValueError(f"unknown key {p!r} in {spec!r}")
        else:
            raise ValueError(f"more than one key in {spec!r}")
    if key is None:
        raise ValueError(f"{spec!r} has no key, only modifiers")
    if not mods and not (0x70 <= key <= 0x87 or key in (0x13, 0x91)):
        raise ValueError(f"{spec!r} needs a modifier (ctrl/alt/shift/win)")
    return mods, key


@dataclass
class Binding:
    name: str
    spec: str
    on_press: Callable[[], None]
    on_release: Callable[[], None] | None = None   # set -> hold mode (called when the key is let go)


class HotkeyManager:
    """Owns the hotkey thread. ``start()`` returns {name: error} for combinations that could not be
    registered (taken by another app, invalid); the rest work."""

    def __init__(self) -> None:
        self._thread: threading.Thread | None = None
        self._tid = 0
        self._bindings: list[Binding] = []
        self._errors: dict[str, str] = {}
        self._ready = threading.Event()

    @property
    def supported(self) -> bool:
        return sys.platform == "win32"

    def start(self, bindings: list[Binding]) -> dict[str, str]:
        self.stop()
        if not self.supported:
            return {b.name: "global hotkeys need Windows" for b in bindings}
        self._bindings = bindings
        self._errors = {}
        self._ready.clear()
        self._thread = threading.Thread(target=self._run, name="hotkeys", daemon=True)
        self._thread.start()
        self._ready.wait(3)
        return dict(self._errors)

    def stop(self) -> None:
        if self._thread is None:
            return
        if self._tid:
            ctypes.windll.user32.PostThreadMessageW(self._tid, WM_QUIT, 0, 0)  # type: ignore[attr-defined]
        self._thread.join(2)
        self._thread = None
        self._tid = 0

    # ------------------------------------------------------------------ thread
    def _run(self) -> None:  # pragma: no cover - Windows only
        from ctypes import wintypes

        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        kernel32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
        self._tid = kernel32.GetCurrentThreadId()
        registered: dict[int, tuple[Binding, int]] = {}
        for i, b in enumerate(self._bindings, start=1):
            try:
                mods, vk = parse_hotkey(b.spec)
            except ValueError as e:
                self._errors[b.name] = str(e)
                continue
            if user32.RegisterHotKey(None, i, mods | MOD_NOREPEAT, vk):
                registered[i] = (b, vk)
                log.info("hotkey %s = %s", b.name, b.spec)
            else:
                self._errors[b.name] = f"{b.spec} is already used by another program"
        self._ready.set()
        msg = wintypes.MSG()
        try:
            while user32.GetMessageW(ctypes.byref(msg), None, 0, 0) > 0:
                if msg.message == WM_HOTKEY and msg.wParam in registered:
                    b, vk = registered[msg.wParam]
                    self._fire(b, vk)
        finally:
            for i in registered:
                user32.UnregisterHotKey(None, i)

    def _fire(self, b: Binding, vk: int) -> None:  # pragma: no cover - Windows only
        try:
            b.on_press()
        except Exception:
            log.exception("hotkey %s failed", b.name)
        if b.on_release is not None:
            threading.Thread(target=self._wait_release, args=(b, vk), name="hotkey-hold", daemon=True).start()

    @staticmethod
    def _wait_release(b: Binding, vk: int) -> None:  # pragma: no cover - Windows only
        user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        deadline = time.monotonic() + 120
        while user32.GetAsyncKeyState(vk) & 0x8000 and time.monotonic() < deadline:
            time.sleep(0.02)
        try:
            assert b.on_release is not None
            b.on_release()
        except Exception:
            log.exception("hotkey %s release failed", b.name)


__all__ = ["Binding", "HotkeyManager", "parse_hotkey"]
