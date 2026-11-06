"""Mouse & keyboard via SendInput (ctypes).

Structures use fixed-width integer types (not ``ctypes.wintypes``, whose
LONG/DWORD are 8 bytes on Linux) so their layout is identical everywhere and
unit tests can assert ``sizeof(INPUT) == 40`` like on 64-bit Windows.

Coordinates are physical pixels: the process is per-monitor DPI aware (Qt
sets that; the CLI calls ``enable_per_monitor_dpi_awareness``), so
``SetCursorPos`` maps 1:1 to screen pixels on every monitor.
"""

from __future__ import annotations

import ctypes
import logging
import threading
import time

from ..core.cancel import CancelToken

log = logging.getLogger(__name__)

LONG = ctypes.c_int32
DWORD = ctypes.c_uint32
WORD = ctypes.c_uint16
ULONG_PTR = ctypes.c_size_t

INPUT_MOUSE, INPUT_KEYBOARD = 0, 1
KEYEVENTF_EXTENDEDKEY, KEYEVENTF_KEYUP, KEYEVENTF_UNICODE, KEYEVENTF_SCANCODE = 0x1, 0x2, 0x4, 0x8
MOUSEEVENTF_MOVE = 0x0001
MOUSE_BUTTONS = {"left": (0x0002, 0x0004), "right": (0x0008, 0x0010), "middle": (0x0020, 0x0040)}
MOUSEEVENTF_WHEEL = 0x0800
WHEEL_DELTA = 120
#: marks our own synthetic events (lets the hotkey hook ignore them later)
EXTRA_INFO_TAG = 0x41535354  # "ASST"


class MOUSEINPUT(ctypes.Structure):
    _fields_ = [("dx", LONG), ("dy", LONG), ("mouseData", DWORD), ("dwFlags", DWORD), ("time", DWORD),
                ("dwExtraInfo", ULONG_PTR)]


class KEYBDINPUT(ctypes.Structure):
    _fields_ = [("wVk", WORD), ("wScan", WORD), ("dwFlags", DWORD), ("time", DWORD), ("dwExtraInfo", ULONG_PTR)]


class HARDWAREINPUT(ctypes.Structure):
    _fields_ = [("uMsg", DWORD), ("wParamL", WORD), ("wParamH", WORD)]


class _INPUTUNION(ctypes.Union):
    _fields_ = [("mi", MOUSEINPUT), ("ki", KEYBDINPUT), ("hi", HARDWAREINPUT)]


class INPUT(ctypes.Structure):
    _anonymous_ = ("u",)
    _fields_ = [("type", DWORD), ("u", _INPUTUNION)]


class POINT(ctypes.Structure):
    _fields_ = [("x", LONG), ("y", LONG)]


VK = {
    "ctrl": 0x11, "alt": 0x12, "shift": 0x10, "win": 0x5B, "enter": 0x0D, "tab": 0x09, "esc": 0x1B,
    "space": 0x20, "backspace": 0x08, "delete": 0x2E, "insert": 0x2D, "home": 0x24, "end": 0x23,
    "pageup": 0x21, "pagedown": 0x22, "up": 0x26, "down": 0x28, "left": 0x25, "right": 0x27,
    "capslock": 0x14, "printscreen": 0x2C, "menu": 0x5D, "volumeup": 0xAF, "volumedown": 0xAE,
    "volumemute": 0xAD, "playpause": 0xB3, "nexttrack": 0xB0, "prevtrack": 0xB1, "mediastop": 0xB2,
    **{f"f{i}": 0x6F + i for i in range(1, 25)},
}
EXTENDED = {0x2D, 0x2E, 0x24, 0x23, 0x21, 0x22, 0x26, 0x28, 0x25, 0x27, 0x5B, 0x5C, 0x5D, 0x2C, 0x6F,
            0xAD, 0xAE, 0xAF, 0xB0, 0xB1, 0xB2, 0xB3, 0xA3, 0xA5}
MODIFIERS = {"ctrl", "alt", "shift", "win"}


def key_input(vk: int, up: bool, scan: int = 0) -> INPUT:
    flags = KEYEVENTF_KEYUP if up else 0
    if vk in EXTENDED:
        flags |= KEYEVENTF_EXTENDEDKEY
    i = INPUT(type=INPUT_KEYBOARD)
    i.ki = KEYBDINPUT(vk, scan, flags, 0, EXTRA_INFO_TAG)
    return i


def unicode_input(code_unit: int, up: bool) -> INPUT:
    i = INPUT(type=INPUT_KEYBOARD)
    i.ki = KEYBDINPUT(0, code_unit, KEYEVENTF_UNICODE | (KEYEVENTF_KEYUP if up else 0), 0, EXTRA_INFO_TAG)
    return i


def mouse_input(flags: int, data: int = 0, dx: int = 0, dy: int = 0) -> INPUT:
    i = INPUT(type=INPUT_MOUSE)
    i.mi = MOUSEINPUT(dx, dy, data & 0xFFFFFFFF, flags, 0, EXTRA_INFO_TAG)
    return i


def text_to_inputs(text: str) -> list[INPUT]:
    """Unicode key events per UTF-16 code unit; newlines/tabs become real Enter/Tab presses."""
    out: list[INPUT] = []
    text = text.replace("\r\n", "\n")
    for ch in text:
        if ch == "\n":
            out += [key_input(0x0D, False), key_input(0x0D, True)]
        elif ch == "\t":
            out += [key_input(0x09, False), key_input(0x09, True)]
        else:
            data = ch.encode("utf-16-le")
            for k in range(0, len(data), 2):
                cu = int.from_bytes(data[k:k + 2], "little")
                out += [unicode_input(cu, False), unicode_input(cu, True)]
    return out


class WinInput:
    CHUNK = 24  # inputs per SendInput call while typing (cancel checks in between)

    def __init__(self) -> None:
        self.user32 = ctypes.windll.user32  # type: ignore[attr-defined]
        u = self.user32
        u.SendInput.argtypes = [ctypes.c_uint32, ctypes.POINTER(INPUT), ctypes.c_int32]
        u.SendInput.restype = ctypes.c_uint32
        u.SetCursorPos.argtypes = [ctypes.c_int32, ctypes.c_int32]
        u.GetCursorPos.argtypes = [ctypes.POINTER(POINT)]
        u.GetSystemMetrics.argtypes = [ctypes.c_int32]
        u.GetSystemMetrics.restype = ctypes.c_int32
        u.MapVirtualKeyW.argtypes = [ctypes.c_uint32, ctypes.c_uint32]
        u.MapVirtualKeyW.restype = ctypes.c_uint32
        u.VkKeyScanW.argtypes = [ctypes.c_wchar]
        u.VkKeyScanW.restype = ctypes.c_int16
        u.GetDoubleClickTime.restype = ctypes.c_uint32
        u.GetAsyncKeyState.argtypes = [ctypes.c_int32]
        u.GetAsyncKeyState.restype = ctypes.c_int16
        self._held_buttons: set[str] = set()
        self._lock = threading.Lock()

    # ---------------------------------------------------------------- primitives
    def _send(self, inputs: list[INPUT]) -> None:
        if not inputs:
            return
        arr = (INPUT * len(inputs))(*inputs)
        sent = self.user32.SendInput(len(inputs), arr, ctypes.sizeof(INPUT))
        if sent != len(inputs):
            raise OSError(f"SendInput inserted {sent}/{len(inputs)} events (blocked by another app or UIPI?)")

    def cursor_pos(self) -> tuple[int, int]:
        p = POINT()
        self.user32.GetCursorPos(ctypes.byref(p))
        return p.x, p.y

    def screen_rect(self) -> tuple[int, int, int, int]:
        g = self.user32.GetSystemMetrics
        return g(76), g(77), g(78), g(79)  # SM_X/YVIRTUALSCREEN, SM_CX/CYVIRTUALSCREEN

    def move(self, x: int, y: int) -> None:
        if not self.user32.SetCursorPos(int(x), int(y)):
            raise OSError("SetCursorPos failed")

    def click(self, x: int | None, y: int | None, button: str = "left", count: int = 1) -> None:
        if x is not None and y is not None:
            self.move(x, y)
            time.sleep(0.03)
        down, up = MOUSE_BUTTONS[button]
        gap = min(0.08, self.user32.GetDoubleClickTime() / 4000)
        for n in range(count):
            self._send([mouse_input(down), mouse_input(up)])
            if n + 1 < count:
                time.sleep(gap)

    def drag(self, x1: int, y1: int, x2: int, y2: int, button: str = "left") -> None:
        down, up = MOUSE_BUTTONS[button]
        self.move(x1, y1)
        time.sleep(0.05)
        with self._lock:
            self._send([mouse_input(down)])
            self._held_buttons.add(button)
        try:
            steps = 20
            for s in range(1, steps + 1):
                self.move(x1 + (x2 - x1) * s // steps, y1 + (y2 - y1) * s // steps)
                time.sleep(0.012)
        finally:
            with self._lock:
                self._send([mouse_input(up)])
                self._held_buttons.discard(button)

    def scroll(self, clicks: int, x: int | None = None, y: int | None = None) -> None:
        if x is not None and y is not None:
            self.move(x, y)
        self._send([mouse_input(MOUSEEVENTF_WHEEL, clicks * WHEEL_DELTA)])

    def nudge(self) -> None:
        """A zero-distance mouse move: makes us 'the last input source' so SetForegroundWindow is allowed."""
        self._send([mouse_input(MOUSEEVENTF_MOVE, 0, 0, 0)])

    # ---------------------------------------------------------------- keyboard
    def _vk(self, key: str) -> int:
        if key in VK:
            return VK[key]
        if len(key) == 1:
            if key.isalnum() and key.isascii():
                return ord(key.upper())
            res = self.user32.VkKeyScanW(key)
            if res == -1:
                raise OSError(f"key {key!r} is not on the current keyboard layout")
            return res & 0xFF
        raise OSError(f"unknown key {key!r}")

    def hotkey(self, keys: list[str]) -> None:
        vks = [self._vk(k) for k in keys]
        downs = [key_input(v, False, self.user32.MapVirtualKeyW(v, 0)) for v in vks]
        ups = [key_input(v, True, self.user32.MapVirtualKeyW(v, 0)) for v in reversed(vks)]
        self._send(downs + ups)  # one atomic batch: nothing can stay stuck

    def type_text(self, text: str, cancel: CancelToken) -> None:
        inputs = text_to_inputs(text)
        for i in range(0, len(inputs), self.CHUNK):
            cancel.check()
            self._send(inputs[i:i + self.CHUNK])
            time.sleep(0.008)

    def release_all(self) -> None:
        """Panic: release anything we may hold (mouse buttons from a drag, modifier keys)."""
        with self._lock:
            ups = [mouse_input(MOUSE_BUTTONS[b][1]) for b in self._held_buttons]
            self._held_buttons.clear()
        for vk in (0xA0, 0xA1, 0xA2, 0xA3, 0xA4, 0xA5, 0x5B, 0x5C):  # L/R shift, ctrl, alt, win
            if self.user32.GetAsyncKeyState(vk) & 0x8000:
                ups.append(key_input(vk, True))
        try:
            self._send(ups)
        except OSError as e:
            log.warning("release_all: %s", e)
