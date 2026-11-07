"""Is the foreground window a fullscreen app? (Windows; used by automatic game mode)."""

from __future__ import annotations

import ctypes
import logging
import os
import sys
from ctypes import wintypes

from ..core.gamemode import ForegroundInfo

log = logging.getLogger(__name__)

PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
MONITOR_DEFAULTTONEAREST = 2
QUNS_RUNNING_D3D_FULL_SCREEN = 3
SHELL_CLASSES = {"Progman", "WorkerW", "Shell_TrayWnd", "Shell_SecondaryTrayWnd", "Windows.UI.Core.CoreWindow"}
API_DLLS = ("d3d11.dll", "d3d12.dll", "vulkan-1.dll", "d3d9.dll", "opengl32.dll", "dxgi.dll")

_3d_cache: dict[tuple[int, str], bool | None] = {}


class MONITORINFO(ctypes.Structure):
    _fields_ = [("cbSize", wintypes.DWORD), ("rcMonitor", wintypes.RECT), ("rcWork", wintypes.RECT),
                ("dwFlags", wintypes.DWORD)]


def foreground_info() -> ForegroundInfo | None:
    if sys.platform != "win32":
        return None
    user32 = ctypes.windll.user32  # type: ignore[attr-defined]
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    cls = ctypes.create_unicode_buffer(128)
    user32.GetClassNameW(hwnd, cls, 128)
    if cls.value in SHELL_CLASSES:
        return ForegroundInfo(False)
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    if pid.value == os.getpid():
        return ForegroundInfo(False)
    rect = wintypes.RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(rect))
    mon = user32.MonitorFromWindow(hwnd, MONITOR_DEFAULTTONEAREST)
    mi = MONITORINFO()
    mi.cbSize = ctypes.sizeof(MONITORINFO)
    if not mon or not user32.GetMonitorInfoW(mon, ctypes.byref(mi)):
        return ForegroundInfo(False)
    m = mi.rcMonitor
    fullscreen = rect.left <= m.left and rect.top <= m.top and rect.right >= m.right and rect.bottom >= m.bottom
    if not fullscreen:
        return ForegroundInfo(False, pid=pid.value)
    title = ctypes.create_unicode_buffer(256)
    user32.GetWindowTextW(hwnd, title, 256)
    exe = _exe_path(pid.value)
    state = ctypes.c_int(0)
    try:
        ctypes.windll.shell32.SHQueryUserNotificationState(ctypes.byref(state))  # type: ignore[attr-defined]
    except (OSError, AttributeError):
        pass
    exclusive = state.value == QUNS_RUNNING_D3D_FULL_SCREEN
    return ForegroundInfo(True, exclusive, exe, title.value, pid.value, _uses_3d(pid.value, exe))


def _exe_path(pid: int) -> str:
    k32 = ctypes.windll.kernel32  # type: ignore[attr-defined]
    h = k32.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not h:
        return ""
    try:
        buf = ctypes.create_unicode_buffer(1024)
        size = wintypes.DWORD(1024)
        if k32.QueryFullProcessImageNameW(h, 0, buf, ctypes.byref(size)):
            return buf.value
        return ""
    finally:
        k32.CloseHandle(h)


def _uses_3d(pid: int, exe: str) -> bool | None:
    """Does the process load a 3D API? Cached per process; None when Windows doesn't let us look."""
    key = (pid, exe)
    if key in _3d_cache:
        return _3d_cache[key]
    result: bool | None = None
    try:
        import psutil

        names = {os.path.basename(m.path).lower() for m in psutil.Process(pid).memory_maps(grouped=True)}
        result = any(d in names for d in API_DLLS)
    except Exception as e:      # anti-cheat protected / elevated games deny this
        log.debug("module list of %s not readable: %s", exe, e)
    if len(_3d_cache) > 64:
        _3d_cache.clear()
    _3d_cache[key] = result
    return result


__all__ = ["foreground_info"]
