"""Small shell-level helpers (process identity, DPI awareness, opening files)."""

from __future__ import annotations

import ctypes
import logging
import os
import subprocess
import sys
from pathlib import Path

from . import IS_WINDOWS

log = logging.getLogger(__name__)

# DPI_AWARENESS_CONTEXT_PER_MONITOR_AWARE_V2 == (HANDLE)-4
_PER_MONITOR_AWARE_V2 = -4


def set_app_user_model_id(app_id: str) -> bool:
    """Give the process an explicit AppUserModelID (taskbar grouping, toast notifications)."""
    if not IS_WINDOWS:
        return False
    try:
        fn = ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        fn.argtypes = [ctypes.c_wchar_p]
        fn.restype = ctypes.c_long
        return fn(app_id) == 0
    except (AttributeError, OSError):  # pragma: no cover
        return False


def enable_per_monitor_dpi_awareness() -> bool:
    """Make the process per-monitor DPI aware (v2) so mouse coordinates are physical pixels.

    Qt does this itself for the GUI; call it only in non-Qt entry points (CLI),
    because a second call fails (harmlessly, but Qt logs a warning).
    """
    if not IS_WINDOWS:
        return False
    user32 = ctypes.windll.user32
    try:
        fn = user32.SetProcessDpiAwarenessContext
        fn.argtypes = [ctypes.c_void_p]
        fn.restype = ctypes.c_int
        if fn(ctypes.c_void_p(_PER_MONITOR_AWARE_V2)):
            return True
    except AttributeError:  # Windows 10 < 1703
        pass
    try:
        return ctypes.windll.shcore.SetProcessDpiAwareness(2) == 0  # PROCESS_PER_MONITOR_DPI_AWARE
    except (AttributeError, OSError):
        return bool(user32.SetProcessDPIAware())


def open_path(path: str | os.PathLike[str], *, edit: bool = False) -> None:
    """Open a file/folder with its default handler (falls back to Notepad for editing text)."""
    p = str(Path(path))
    if IS_WINDOWS:
        verbs = ("edit", "open") if edit else ("open",)
        for verb in verbs:
            try:
                os.startfile(p, verb)  # type: ignore[attr-defined]
                return
            except OSError:
                if not edit and verb == verbs[-1]:
                    raise
        subprocess.Popen(["notepad.exe", p])  # no association for .toml -> Notepad
        return
    opener = "open" if sys.platform == "darwin" else "xdg-open"
    try:
        subprocess.Popen([opener, p], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        log.warning("cannot open %s: %s not installed", p, opener)
