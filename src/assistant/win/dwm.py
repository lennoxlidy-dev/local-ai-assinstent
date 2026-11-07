"""Window styling for the popup on Windows: acrylic blur, rounded clip, never-activate.

Everything here is best effort: each call returns False (and logs at debug level)
when the API is missing (older Windows, other OS) so the popup falls back to a
plain translucent card.
"""

from __future__ import annotations

import ctypes
import logging
import sys

log = logging.getLogger(__name__)

GWL_EXSTYLE = -20
WS_EX_TOOLWINDOW = 0x00000080
WS_EX_NOACTIVATE = 0x08000000
SWP_NOSIZE, SWP_NOMOVE, SWP_NOZORDER, SWP_NOACTIVATE, SWP_FRAMECHANGED = 0x1, 0x2, 0x4, 0x10, 0x20

WCA_ACCENT_POLICY = 19
ACCENT_DISABLED = 0
ACCENT_ENABLE_BLURBEHIND = 3
ACCENT_ENABLE_ACRYLICBLURBEHIND = 4   # Windows 10 1803+ (build 17134)

DWMWA_USE_IMMERSIVE_DARK_MODE = 20
DWMWA_WINDOW_CORNER_PREFERENCE = 33   # Windows 11
DWMWCP_DONOTROUND = 1


class ACCENT_POLICY(ctypes.Structure):
    _fields_ = [("AccentState", ctypes.c_int), ("AccentFlags", ctypes.c_int),
                ("GradientColor", ctypes.c_uint32), ("AnimationId", ctypes.c_int)]


class WINDOWCOMPOSITIONATTRIBDATA(ctypes.Structure):
    _fields_ = [("Attribute", ctypes.c_int), ("Data", ctypes.c_void_p), ("SizeOfData", ctypes.c_size_t)]


def windows_build() -> int:
    if sys.platform != "win32":
        return 0
    return sys.getwindowsversion().build


def _user32():  # type: ignore[no-untyped-def]
    return ctypes.WinDLL("user32", use_last_error=True)  # type: ignore[attr-defined]


def set_acrylic(hwnd: int, on: bool, tint_rgb: int = 0x141418, tint_alpha: int = 0x40) -> bool:
    """Blur what is behind the window (acrylic on 10 1803+/11, plain blur before). The QML card draws
    its own translucent tint on top, so the system tint stays light."""
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        fn = getattr(_user32(), "SetWindowCompositionAttribute", None)
        if fn is None:
            return False
        state = ACCENT_DISABLED
        if on:
            state = ACCENT_ENABLE_ACRYLICBLURBEHIND if windows_build() >= 17134 else ACCENT_ENABLE_BLURBEHIND
        r, g, b = (tint_rgb >> 16) & 0xFF, (tint_rgb >> 8) & 0xFF, tint_rgb & 0xFF
        accent = ACCENT_POLICY(state, 2 if on else 0, (tint_alpha << 24) | (b << 16) | (g << 8) | r, 0)
        data = WINDOWCOMPOSITIONATTRIBDATA(WCA_ACCENT_POLICY, ctypes.cast(ctypes.pointer(accent), ctypes.c_void_p),
                                           ctypes.sizeof(accent))
        ok = bool(fn(ctypes.c_void_p(hwnd), ctypes.byref(data)))
        log.debug("acrylic %s -> %s", "on" if on else "off", ok)
        return ok and on
    except Exception as e:  # pragma: no cover - Windows only
        log.debug("acrylic not available: %s", e)
        return False


def set_round_region(hwnd: int, width_px: int, height_px: int, radius_px: int) -> bool:
    """Clip the window to a rounded rectangle so the blur does not show outside the card's corners.
    Pass radius_px=0 to remove the clip."""
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        user32 = _user32()
        if radius_px <= 0:
            return bool(user32.SetWindowRgn(ctypes.c_void_p(hwnd), None, True))
        gdi32 = ctypes.WinDLL("gdi32", use_last_error=True)  # type: ignore[attr-defined]
        gdi32.CreateRoundRectRgn.restype = ctypes.c_void_p
        rgn = gdi32.CreateRoundRectRgn(0, 0, width_px + 1, height_px + 1, radius_px * 2, radius_px * 2)
        if not rgn:
            return False
        if not user32.SetWindowRgn(ctypes.c_void_p(hwnd), ctypes.c_void_p(rgn), True):
            gdi32.DeleteObject(ctypes.c_void_p(rgn))   # on success the system owns the region
            return False
        return True
    except Exception as e:  # pragma: no cover - Windows only
        log.debug("window region failed: %s", e)
        return False


def set_no_activate(hwnd: int, on: bool) -> bool:
    """WS_EX_NOACTIVATE: clicks and showing never take the focus from the app you are using.
    Removed while the mini chat is open so you can type into it."""
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        user32 = _user32()
        get = user32.GetWindowLongPtrW
        put = user32.SetWindowLongPtrW
        get.restype = ctypes.c_ssize_t
        get.argtypes = [ctypes.c_void_p, ctypes.c_int]
        put.restype = ctypes.c_ssize_t
        put.argtypes = [ctypes.c_void_p, ctypes.c_int, ctypes.c_ssize_t]
        ex = get(ctypes.c_void_p(hwnd), GWL_EXSTYLE)
        new = (ex | WS_EX_NOACTIVATE | WS_EX_TOOLWINDOW) if on else (ex & ~WS_EX_NOACTIVATE)
        if new != ex:
            put(ctypes.c_void_p(hwnd), GWL_EXSTYLE, new)
            user32.SetWindowPos(ctypes.c_void_p(hwnd), None, 0, 0, 0, 0,
                                SWP_NOSIZE | SWP_NOMOVE | SWP_NOZORDER | SWP_NOACTIVATE | SWP_FRAMECHANGED)
        return True
    except Exception as e:  # pragma: no cover - Windows only
        log.debug("no-activate style failed: %s", e)
        return False


def disable_system_corners(hwnd: int) -> bool:
    """Windows 11 would round the window itself with a different radius; the card draws its own."""
    if windows_build() < 22000 or not hwnd:
        return False
    try:
        dwmapi = ctypes.WinDLL("dwmapi")  # type: ignore[attr-defined]
        pref = ctypes.c_int(DWMWCP_DONOTROUND)
        return dwmapi.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), DWMWA_WINDOW_CORNER_PREFERENCE,
                                            ctypes.byref(pref), ctypes.sizeof(pref)) == 0
    except Exception as e:  # pragma: no cover - Windows only
        log.debug("corner preference failed: %s", e)
        return False


def set_dark_titlebar(hwnd: int, dark: bool) -> bool:
    """Dark title bar for normal windows (setup wizard, settings) on Windows 10 2004+ / 11."""
    if sys.platform != "win32" or not hwnd:
        return False
    try:
        dwmapi = ctypes.WinDLL("dwmapi")  # type: ignore[attr-defined]
        val = ctypes.c_int(1 if dark else 0)
        return dwmapi.DwmSetWindowAttribute(ctypes.c_void_p(hwnd), DWMWA_USE_IMMERSIVE_DARK_MODE,
                                            ctypes.byref(val), ctypes.sizeof(val)) == 0
    except Exception as e:  # pragma: no cover - Windows only
        log.debug("dark title bar failed: %s", e)
        return False


__all__ = ["disable_system_corners", "set_acrylic", "set_dark_titlebar", "set_no_activate", "set_round_region",
           "windows_build"]
