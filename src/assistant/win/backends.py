"""Windows implementations of the tool backends (see ``tools/platform.py``).

Each backend is created defensively: if one fails to load (missing package,
unsupported Windows build), only that feature reports "not available" and
everything else keeps working.
"""

from __future__ import annotations

import base64
import ctypes
import json
import logging
import os
import re
import subprocess
import tempfile
import threading
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any

import psutil

from ..core.cancel import CancelToken
from ..core.proc import hidden_flags, run_hidden
from ..tools.base import ToolError
from ..tools.platform import (AppEntry, CommandOutput, DesktopInfo, OcrLine, Platform, UiElement, Unsupported,
                              WindowInfo)
from .com import WinRTWorker, ensure_com
from .inputs import WinInput, key_input

log = logging.getLogger(__name__)

# =============================================================================== known folders

_FOLDERIDS = {
    "desktop": "B4BFCC3A-DB2C-424C-B029-7FE99A87C641",
    "documents": "FDD39AD0-238F-46AF-ADB4-6C85480369C7",
    "downloads": "374DE290-123F-4565-9164-39C4925E467B",
    "music": "4BD8D571-6D19-48D3-BE97-422220080E43",
    "pictures": "33E28130-4E1E-4676-835A-98395C3BC3BB",
    "videos": "18989B1D-99B5-455B-841C-AB7C74E4DDFC",
    "home": "5E6C858F-0E22-4760-9AFE-EA3317B67173",
}


class _GUID(ctypes.Structure):
    _fields_ = [("Data1", ctypes.c_uint32), ("Data2", ctypes.c_uint16), ("Data3", ctypes.c_uint16),
                ("Data4", ctypes.c_ubyte * 8)]


def _guid(s: str) -> _GUID:
    u = uuid.UUID(s)
    g = _GUID(u.fields[0], u.fields[1], u.fields[2])
    g.Data4[:] = list(u.bytes[8:])
    return g


def known_folders() -> dict[str, str]:
    """Real locations (OneDrive-redirected Desktop/Documents are handled)."""
    shell32 = ctypes.windll.shell32  # type: ignore[attr-defined]
    ole32 = ctypes.windll.ole32  # type: ignore[attr-defined]
    fn = shell32.SHGetKnownFolderPath
    fn.argtypes = [ctypes.POINTER(_GUID), ctypes.c_uint32, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p)]
    fn.restype = ctypes.c_long
    ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]
    out: dict[str, str] = {}
    for name, gid in _FOLDERIDS.items():
        ptr = ctypes.c_void_p()
        g = _guid(gid)
        if fn(ctypes.byref(g), 0, None, ctypes.byref(ptr)) == 0 and ptr.value:
            out[name] = ctypes.wstring_at(ptr.value)
        if ptr.value:
            ole32.CoTaskMemFree(ptr)
    return out


# =============================================================================== windows

_JUNK_TITLES = {"program manager", "windows input experience", "microsoft text input application",
                "nvidia geforce overlay"}
DWMWA_CLOAKED, DWMWA_EXTENDED_FRAME_BOUNDS = 14, 9
DWM_CLOAKED_SHELL = 0x2


class _RECT(ctypes.Structure):
    _fields_ = [("left", ctypes.c_int32), ("top", ctypes.c_int32), ("right", ctypes.c_int32),
                ("bottom", ctypes.c_int32)]


class WinWindows:
    PROTECTED = {"csrss.exe", "wininit.exe", "winlogon.exe", "services.exe", "lsass.exe", "smss.exe",
                 "svchost.exe", "dwm.exe", "explorer.exe", "system", "registry", "fontdrvhost.exe"}

    def __init__(self, nudge: Any = None) -> None:
        import win32gui  # noqa: F401  (fail early if pywin32 is missing)

        self._nudge = nudge
        self._dwm = ctypes.windll.dwmapi  # type: ignore[attr-defined]
        self._dwm.DwmGetWindowAttribute.argtypes = [ctypes.c_void_p, ctypes.c_uint32, ctypes.c_void_p,
                                                    ctypes.c_uint32]
        self._dwm.DwmGetWindowAttribute.restype = ctypes.c_long
        self._exe_cache: dict[int, str] = {}

    def _cloaked(self, hwnd: int) -> int:
        val = ctypes.c_int32(0)
        self._dwm.DwmGetWindowAttribute(ctypes.c_void_p(hwnd), DWMWA_CLOAKED, ctypes.byref(val), 4)
        return val.value

    def _exe(self, pid: int) -> str:
        if pid not in self._exe_cache:
            try:
                self._exe_cache[pid] = psutil.Process(pid).name()
            except (psutil.Error, OSError):
                self._exe_cache[pid] = ""
            if len(self._exe_cache) > 512:
                self._exe_cache.clear()
        return self._exe_cache.get(pid, "")

    def _uwp_child_pid(self, hwnd: int, frame_pid: int) -> int | None:
        import win32gui
        import win32process

        found: list[int] = []

        def cb(child: int, _: Any) -> bool:
            _, pid = win32process.GetWindowThreadProcessId(child)
            if pid != frame_pid:
                found.append(pid)
                return False
            return True

        try:
            win32gui.EnumChildWindows(hwnd, cb, None)
        except Exception:  # EnumChildWindows raises when the callback stops early
            pass
        return found[0] if found else None

    def _info(self, hwnd: int) -> WindowInfo | None:
        import win32gui
        import win32process

        if not win32gui.IsWindow(hwnd):
            return None
        title = win32gui.GetWindowText(hwnd)
        _, pid = win32process.GetWindowThreadProcessId(hwnd)
        exe = self._exe(pid)
        if exe.lower() == "applicationframehost.exe":
            child = self._uwp_child_pid(hwnd, pid)
            if child:
                pid, exe = child, self._exe(child)
        cloak = self._cloaked(hwnd)
        return WindowInfo(hwnd=hwnd, title=title, pid=pid, exe=exe, minimized=bool(win32gui.IsIconic(hwnd)),
                          cloaked=bool(cloak), desktop=None)

    def list_windows(self) -> list[WindowInfo]:
        import win32con
        import win32gui

        hwnds: list[int] = []

        def cb(hwnd: int, _: Any) -> bool:
            if not win32gui.IsWindowVisible(hwnd) or win32gui.GetWindow(hwnd, win32con.GW_OWNER):
                return True
            ex = win32gui.GetWindowLong(hwnd, win32con.GWL_EXSTYLE)
            if ex & win32con.WS_EX_TOOLWINDOW and not ex & win32con.WS_EX_APPWINDOW:
                return True
            title = win32gui.GetWindowText(hwnd)
            if not title or title.strip().lower() in _JUNK_TITLES:
                return True
            cloak = self._cloaked(hwnd)
            if cloak and cloak != DWM_CLOAKED_SHELL:  # hidden UWP frames etc.; SHELL = other virtual desktop
                return True
            hwnds.append(hwnd)
            return True

        win32gui.EnumWindows(cb, None)
        out = [self._info(h) for h in hwnds]
        return [w for w in out if w is not None and w.exe.lower() not in ("textinputhost.exe",)]

    def foreground(self) -> WindowInfo | None:
        import win32gui

        h = win32gui.GetForegroundWindow()
        return self._info(h) if h else None

    def focus(self, hwnd: int) -> bool:
        import win32con
        import win32gui
        import win32process

        if win32gui.IsIconic(hwnd):
            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
        for attempt in range(3):
            try:
                if self._nudge is not None:
                    self._nudge()
                win32gui.SetForegroundWindow(hwnd)
            except Exception:
                if attempt == 1:  # classic fallback: attach to the foreground thread's input queue
                    fg = win32gui.GetForegroundWindow()
                    fg_thread = win32process.GetWindowThreadProcessId(fg)[0] if fg else 0
                    me = ctypes.windll.kernel32.GetCurrentThreadId()  # type: ignore[attr-defined]
                    if fg_thread and fg_thread != me:
                        attach = ctypes.windll.user32.AttachThreadInput  # type: ignore[attr-defined]
                        attach(me, fg_thread, True)
                        try:
                            win32gui.BringWindowToTop(hwnd)
                            win32gui.SetForegroundWindow(hwnd)
                        except Exception:
                            pass
                        finally:
                            attach(me, fg_thread, False)
            if win32gui.GetForegroundWindow() == hwnd:
                return True
            time.sleep(0.05)
        try:  # last resort: what Alt+Tab does
            from pyvda import AppView

            AppView(hwnd=hwnd).switch_to()
            time.sleep(0.1)
        except Exception:
            pass
        return win32gui.GetForegroundWindow() == hwnd

    def close(self, hwnd: int) -> None:
        import win32con
        import win32gui

        win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)

    def set_state(self, hwnd: int, state: str) -> None:
        import win32con
        import win32gui

        cmd = {"minimize": win32con.SW_MINIMIZE, "maximize": win32con.SW_MAXIMIZE,
               "restore": win32con.SW_RESTORE}[state]
        win32gui.ShowWindow(hwnd, cmd)

    def rect(self, hwnd: int) -> tuple[int, int, int, int]:
        r = _RECT()
        if self._dwm.DwmGetWindowAttribute(ctypes.c_void_p(hwnd), DWMWA_EXTENDED_FRAME_BOUNDS, ctypes.byref(r),
                                           ctypes.sizeof(r)) != 0:
            import win32gui

            r.left, r.top, r.right, r.bottom = win32gui.GetWindowRect(hwnd)
        return r.left, r.top, r.right - r.left, r.bottom - r.top

    def kill(self, pid: int) -> None:
        if pid in (0, 4, os.getpid()):
            raise ToolError("I won't kill that process.")
        proc = psutil.Process(pid)
        if proc.name().lower() in self.PROTECTED:
            raise ToolError(f"{proc.name()} is a Windows system process; killing it is not allowed.")
        proc.kill()


# =============================================================================== apps

KNOWN_EXE = {"spotify": "Spotify.exe", "discord": "Discord.exe", "steam": "steam.exe", "google chrome": "chrome.exe",
             "chrome": "chrome.exe", "microsoft edge": "msedge.exe", "firefox": "firefox.exe",
             "visual studio code": "Code.exe", "microsoft teams": "ms-teams.exe", "whatsapp": "WhatsApp.exe",
             "telegram": "Telegram.exe", "notepad": "Notepad.exe", "file explorer": "explorer.exe",
             "obs studio": "obs64.exe", "vlc media player": "vlc.exe", "slack": "slack.exe",
             "signal": "Signal.exe", "epic games launcher": "EpicGamesLauncher.exe", "battle.net": "Battle.net.exe",
             "calculator": "CalculatorApp.exe", "outlook": "olk.exe"}


class WinApps:
    CACHE_TTL_S = 24 * 3600

    def __init__(self, cache_dir: Path):
        self.cache = Path(cache_dir) / "apps.json"
        self._lock = threading.Lock()

    def list_apps(self, refresh: bool = False) -> list[AppEntry]:
        with self._lock:
            if not refresh and self.cache.exists() and time.time() - self.cache.stat().st_mtime < self.CACHE_TTL_S:
                try:
                    return [AppEntry(**d) for d in json.loads(self.cache.read_text(encoding="utf-8"))]
                except (ValueError, TypeError):
                    pass
            apps = self._get_start_apps() or self._scan_start_menu()
            self.cache.parent.mkdir(parents=True, exist_ok=True)
            self.cache.write_text(json.dumps([a.__dict__ for a in apps], ensure_ascii=False), encoding="utf-8")
            log.info("app index: %d apps", len(apps))
            return apps

    @staticmethod
    def _entry(name: str, app_id: str) -> AppEntry:
        kind = "store" if "!" in app_id else "desktop"
        if re.match(r"^https?://", app_id, re.I):
            kind = "url"
        m = re.search(r"([\w .()-]+\.exe)$", app_id, re.I) or re.search(r"\b(\w+\.exe)\b", app_id, re.I)
        exe = m.group(1).split("\\")[-1] if m else KNOWN_EXE.get(name.casefold(), "")
        return AppEntry(name=name, app_id=app_id, kind=kind, exe=exe)

    def _get_start_apps(self) -> list[AppEntry]:
        script = ("[Console]::OutputEncoding=[Text.Encoding]::UTF8; "
                  "Get-StartApps | Select-Object Name,AppID | ConvertTo-Json -Compress")
        try:
            res = run_hidden(["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", script], timeout=30)
            data = json.loads(res.stdout or "[]")
        except (OSError, ValueError, subprocess.TimeoutExpired) as e:
            log.warning("Get-StartApps failed: %s", e)
            return []
        if isinstance(data, dict):
            data = [data]
        out, seen = [], set()
        for d in data:
            name, app_id = str(d.get("Name") or "").strip(), str(d.get("AppID") or "").strip()
            if not name or not app_id or (name.casefold(), app_id) in seen:
                continue
            if re.search(r"\b(uninstall|deinstall|readme|help|documentation|website)\b", name, re.I):
                continue
            seen.add((name.casefold(), app_id))
            out.append(self._entry(name, app_id))
        return out

    def _scan_start_menu(self) -> list[AppEntry]:
        roots = [Path(os.environ.get("APPDATA", "")) / "Microsoft/Windows/Start Menu/Programs",
                 Path(os.environ.get("PROGRAMDATA", "C:/ProgramData")) / "Microsoft/Windows/Start Menu/Programs"]
        out = []
        for root in roots:
            for lnk in root.rglob("*.lnk"):
                if not re.search(r"uninstall|deinstall", lnk.stem, re.I):
                    out.append(AppEntry(lnk.stem, str(lnk), "desktop", KNOWN_EXE.get(lnk.stem.casefold(), "")))
        return out

    def launch(self, app: AppEntry, arguments: str = "") -> None:
        target = app.app_id
        if app.kind == "url" or target.lower().endswith(".lnk"):
            os.startfile(target)  # type: ignore[attr-defined]
            return
        try:
            os.startfile("shell:AppsFolder\\" + target)  # type: ignore[attr-defined]
        except OSError:
            subprocess.Popen(["explorer.exe", "shell:AppsFolder\\" + target], creationflags=hidden_flags())

    def open_target(self, target: str) -> None:
        os.startfile(target)  # type: ignore[attr-defined]


# =============================================================================== virtual desktops

class WinDesktops:
    """pyvda (undocumented COM APIs that change between builds). Every call is guarded."""

    def __init__(self) -> None:
        import pyvda  # noqa: F401  (raises NotImplementedError on unsupported builds)

        ensure_com()
        pyvda.get_virtual_desktops()  # probe once so a broken build fails here, not mid-command

    def list(self) -> list[DesktopInfo]:
        from pyvda import VirtualDesktop, get_virtual_desktops

        ensure_com()
        cur = VirtualDesktop.current().id
        out = []
        for i, vd in enumerate(get_virtual_desktops(), 1):
            try:
                name = vd.name
            except Exception:
                name = ""
            out.append(DesktopInfo(i, name, vd.id == cur))
        return out

    def switch(self, number: int) -> None:
        from pyvda import VirtualDesktop

        ensure_com()
        VirtualDesktop(number).go()

    def create(self, name: str) -> DesktopInfo:
        from pyvda import VirtualDesktop

        ensure_com()
        vd = VirtualDesktop.create()
        if name:
            try:
                vd.rename(name)
            except Exception as e:  # Windows 10 has no desktop names
                log.info("cannot name desktops on this Windows version: %s", e)
                name = ""
        return DesktopInfo(vd.number, name, False)

    def move_window(self, hwnd: int, number: int) -> None:
        from pyvda import AppView, VirtualDesktop

        ensure_com()
        AppView(hwnd=hwnd).move(VirtualDesktop(number))

    def window_desktop(self, hwnd: int) -> int | None:
        from pyvda import AppView

        ensure_com()
        try:
            return AppView(hwnd=hwnd).desktop.number
        except Exception:
            return None


# =============================================================================== media (SMTC)

class WinMedia:
    _MEDIA_VK = {"toggle": 0xB3, "play": 0xB3, "pause": 0xB3, "next": 0xB0, "previous": 0xB1, "stop": 0xB2}
    _STATUS = {0: "closed", 1: "opened", 2: "changing", 3: "stopped", 4: "playing", 5: "paused"}

    def __init__(self, send_key: Any = None) -> None:
        self._send_key = send_key
        try:
            import winrt.windows.media.control  # noqa: F401

            self._smtc = True
        except ImportError:
            self._smtc = False

    async def _session(self) -> Any:
        from winrt.windows.media.control import GlobalSystemMediaTransportControlsSessionManager as M

        mgr = await M.request_async()
        return mgr.get_current_session()

    def command(self, action: str) -> bool:
        if self._smtc:
            async def go() -> bool | None:
                s = await self._session()
                if s is None:
                    return None
                fn = {"play": s.try_play_async, "pause": s.try_pause_async, "toggle": s.try_toggle_play_pause_async,
                      "next": s.try_skip_next_async, "previous": s.try_skip_previous_async,
                      "stop": s.try_stop_async}[action]
                return bool(await fn())

            try:
                res = WinRTWorker.get().run(go, 5)
                if res is not None:
                    return res
            except Exception as e:
                log.debug("SMTC %s failed: %s", action, e)
        if self._send_key is not None:  # fallback: hardware media keys (work without focus)
            self._send_key(self._MEDIA_VK[action])
            return True
        return False

    def status(self) -> dict[str, Any] | None:
        if not self._smtc:
            return None

        async def go() -> dict[str, Any] | None:
            s = await self._session()
            if s is None:
                return None
            props = await s.try_get_media_properties_async()
            info = s.get_playback_info()
            return {"title": props.title if props else "", "artist": props.artist if props else "",
                    "app": s.source_app_user_model_id, "status": self._STATUS.get(int(info.playback_status), "")}

        try:
            return WinRTWorker.get().run(go, 5)
        except Exception as e:
            log.debug("SMTC status failed: %s", e)
            return None


# =============================================================================== audio / display / clipboard / power

class WinAudio:
    def __init__(self) -> None:
        import pycaw.pycaw  # noqa: F401

    def _ep(self) -> Any:
        from pycaw.pycaw import AudioUtilities

        ensure_com()
        return AudioUtilities.GetSpeakers().EndpointVolume

    def get_volume(self) -> int:
        return round(self._ep().GetMasterVolumeLevelScalar() * 100)

    def set_volume(self, percent: int) -> None:
        self._ep().SetMasterVolumeLevelScalar(max(0, min(100, percent)) / 100.0, None)

    def get_mute(self) -> bool:
        return bool(self._ep().GetMute())

    def set_mute(self, mute: bool) -> None:
        self._ep().SetMute(1 if mute else 0, None)


class WinSound:
    """Sound files through Windows' MCI player: MP3/WAV/WMA without a window or extra packages. Each sound runs
    on its own thread (MCI devices belong to the thread that opened them) and polls for stop/cancel."""

    MAX_S = 900.0        # a routine's sound is never longer than 15 minutes

    def __init__(self) -> None:
        import ctypes

        winmm = ctypes.WinDLL("winmm")
        self._send = winmm.mciSendStringW
        self._send.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p]
        self._send.restype = ctypes.c_uint
        self._error = winmm.mciGetErrorStringW
        self._error.argtypes = [ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_uint]
        self._lock = threading.Lock()
        self._stops: set[threading.Event] = set()
        self._n = 0

    def _cmd(self, command: str) -> str:
        import ctypes

        out = ctypes.create_unicode_buffer(256)
        err = self._send(command, out, 256, None)
        if err:
            msg = ctypes.create_unicode_buffer(256)
            self._error(err, msg, 256)
            raise OSError(msg.value or f"MCI error {err}")
        return out.value

    def play(self, path: str, wait: bool, cancelled: Callable[[], bool] | None = None, *,
             until: Callable[[], bool] | None = None, min_s: float = 0.0) -> None:
        from ..audio.mci import play_session

        stop, done, errors = threading.Event(), threading.Event(), []
        with self._lock:
            self._n += 1
            alias = f"assistant_sound_{self._n}"
            self._stops.add(stop)

        def run() -> None:
            try:
                play_session(self._cmd, alias, path, stop=stop, cancelled=cancelled, until=until, min_s=min_s,
                             max_s=self.MAX_S)
            except OSError as e:
                errors.append(e)
            finally:
                with self._lock:
                    self._stops.discard(stop)
                done.set()

        threading.Thread(target=run, name="sound", daemon=True).start()
        done.wait(None if wait else 0.5)     # (not waiting: still report a file that can't be played)
        if errors:
            raise ToolError(f"Can't play {Path(path).name}: {errors[0]}")

    def stop_all(self) -> None:
        with self._lock:
            for stop in self._stops:
                stop.set()


class WinDisplay:
    """Laptop panel brightness through WMI (external monitors would need DDC/CI)."""

    def _wmi(self) -> Any:
        import win32com.client

        ensure_com()
        return win32com.client.GetObject(r"winmgmts:\\.\root\WMI")

    def get_brightness(self) -> int | None:
        try:
            for m in self._wmi().InstancesOf("WmiMonitorBrightness"):
                return int(m.CurrentBrightness)
        except Exception as e:
            log.debug("brightness read failed: %s", e)
        return None

    def set_brightness(self, percent: int) -> None:
        try:
            methods = list(self._wmi().InstancesOf("WmiMonitorBrightnessMethods"))
        except Exception as e:
            raise ToolError(f"Brightness control isn't available here ({e}).") from None
        if not methods:
            raise ToolError("This display doesn't support brightness control (external monitor?).")
        for m in methods:
            m.WmiSetBrightness(1, int(percent))


class WinClipboard:
    def _open(self) -> Any:
        import win32clipboard

        for _ in range(10):
            try:
                win32clipboard.OpenClipboard()
                return win32clipboard
            except Exception:
                time.sleep(0.05)
        raise ToolError("The clipboard is busy (another app is using it).")

    def get_text(self) -> str:
        cb = self._open()
        try:
            if cb.IsClipboardFormatAvailable(cb.CF_UNICODETEXT):
                return str(cb.GetClipboardData(cb.CF_UNICODETEXT))
            return ""
        finally:
            cb.CloseClipboard()

    def set_text(self, text: str) -> None:
        cb = self._open()
        try:
            cb.EmptyClipboard()
            cb.SetClipboardData(cb.CF_UNICODETEXT, text)
        finally:
            cb.CloseClipboard()


class WinPower:
    def lock(self) -> None:
        if not ctypes.windll.user32.LockWorkStation():  # type: ignore[attr-defined]
            raise ToolError("Windows refused to lock.")

    def sleep(self) -> None:
        fn = ctypes.windll.powrprof.SetSuspendState  # type: ignore[attr-defined]
        fn.argtypes = [ctypes.c_ubyte, ctypes.c_ubyte, ctypes.c_ubyte]
        fn(0, 0, 0)

    def shutdown(self) -> None:
        subprocess.Popen(["shutdown.exe", "/s", "/t", "5"], creationflags=hidden_flags())

    def restart(self) -> None:
        subprocess.Popen(["shutdown.exe", "/r", "/t", "5"], creationflags=hidden_flags())

    def sign_out(self) -> None:
        subprocess.Popen(["shutdown.exe", "/l"], creationflags=hidden_flags())


# =============================================================================== screen + OCR

class WinScreen:
    def __init__(self) -> None:
        import mss  # noqa: F401

    def screenshot_png(self, region: tuple[int, int, int, int] | None = None) -> tuple[bytes, int, int]:
        import mss
        import mss.tools

        with mss.mss() as sct:
            mon = sct.monitors[0] if region is None else {"left": region[0], "top": region[1],
                                                            "width": region[2], "height": region[3]}
            img = sct.grab(mon)
            png = mss.tools.to_png(img.rgb, img.size)
            assert png is not None
            if region is None:
                self.origin = (mon["left"], mon["top"])
            else:
                self.origin = (region[0], region[1])
            return png, img.size[0], img.size[1]

    def ocr(self, png: bytes, language: str) -> list[OcrLine]:
        origin = getattr(self, "origin", (0, 0))

        async def go() -> list[OcrLine]:
            from winrt.windows.globalization import Language
            from winrt.windows.graphics.imaging import BitmapAlphaMode, BitmapDecoder, BitmapPixelFormat
            from winrt.windows.media.ocr import OcrEngine
            from winrt.windows.storage.streams import DataWriter, InMemoryRandomAccessStream

            stream = InMemoryRandomAccessStream()
            writer = DataWriter(stream)
            writer.write_bytes(png)
            await writer.store_async()
            writer.detach_stream()
            stream.seek(0)
            decoder = await BitmapDecoder.create_async(stream)
            bitmap = await decoder.get_software_bitmap_async(BitmapPixelFormat.BGRA8, BitmapAlphaMode.PREMULTIPLIED)
            tag = {"de": "de-DE", "en": "en-US"}.get(language, language)
            engine = None
            try:
                lang = Language(tag)
                if OcrEngine.is_language_supported(lang):
                    engine = OcrEngine.try_create_from_language(lang)
            except Exception:
                engine = None
            engine = engine or OcrEngine.try_create_from_user_profile_languages()
            if engine is None:
                raise ToolError("No Windows OCR language is installed (Settings > Time & language > Language).")
            result = await engine.recognize_async(bitmap)
            lines = []
            for line in result.lines:
                rects = [w.bounding_rect for w in line.words]
                if not rects:
                    continue
                x0 = min(r.x for r in rects)
                y0 = min(r.y for r in rects)
                x1 = max(r.x + r.width for r in rects)
                y1 = max(r.y + r.height for r in rects)
                lines.append(OcrLine(line.text, int(x0) + origin[0], int(y0) + origin[1], int(x1 - x0),
                                     int(y1 - y0)))
            return lines

        return WinRTWorker.get().run(go, 20)


# =============================================================================== UI Automation

class WinUia:
    TIME_BUDGET_S = 3.0

    def __init__(self, click: Any = None) -> None:
        import uiautomation  # noqa: F401

        self._click = click
        self._last: dict[tuple[str, str, int, int], Any] = {}

    def elements(self, hwnd: int | None, limit: int, cancel: CancelToken) -> list[UiElement]:
        import uiautomation as auto

        ensure_com()
        root = auto.ControlFromHandle(hwnd) if hwnd else auto.GetForegroundControl()
        if root is None:
            raise ToolError("That window isn't accessible.")
        out: list[UiElement] = []
        self._last = {}
        t0 = time.monotonic()
        for ctrl, _depth in auto.WalkControl(root, includeTop=False, maxDepth=40):
            if cancel.cancelled or len(out) >= limit or time.monotonic() - t0 > self.TIME_BUDGET_S:
                break
            try:
                name = ctrl.Name
                if not name or ctrl.IsOffscreen:
                    continue
                r = ctrl.BoundingRectangle
                if r.width() <= 0 or r.height() <= 0:
                    continue
                el = UiElement(name=name, control_type=ctrl.ControlTypeName, x=r.left, y=r.top, w=r.width(),
                               h=r.height(), enabled=bool(ctrl.IsEnabled), automation_id=ctrl.AutomationId or "")
            except Exception:
                continue
            out.append(el)
            self._last[(el.name, el.control_type, el.x, el.y)] = ctrl
        return out

    def activate(self, element: UiElement, hwnd: int | None) -> str:
        import uiautomation as auto

        ensure_com()
        ctrl = self._last.get((element.name, element.control_type, element.x, element.y))
        if ctrl is not None:
            for pid, method, label in ((auto.PatternId.InvokePattern, "Invoke", "invoke"),
                                       (auto.PatternId.TogglePattern, "Toggle", "toggle"),
                                       (auto.PatternId.SelectionItemPattern, "Select", "select"),
                                       (auto.PatternId.ExpandCollapsePattern, "Expand", "expand"),
                                       (auto.PatternId.LegacyIAccessiblePattern, "DoDefaultAction",
                                        "default action")):
                try:
                    pat = ctrl.GetPattern(pid)
                    if pat is not None:
                        getattr(pat, method)()
                        return label
                except Exception as e:
                    log.debug("UIA %s failed: %s", label, e)
        if self._click is None:
            raise ToolError("I found the control but couldn't activate it.")
        x, y = element.center
        self._click(x, y, "left", 1)
        return "mouse click"


# =============================================================================== shell

class WinShell:
    def __init__(self) -> None:
        import shutil

        self.pwsh = shutil.which("pwsh.exe")
        self.powershell = shutil.which("powershell.exe") or r"C:\Windows\System32\WindowsPowerShell\v1.0\powershell.exe"

    @staticmethod
    def _encode(script: str) -> str:
        prefix = "[Console]::OutputEncoding=[Text.Encoding]::UTF8;$OutputEncoding=[Text.Encoding]::UTF8;$ProgressPreference='SilentlyContinue';"
        return base64.b64encode((prefix + script).encode("utf-16-le")).decode("ascii")

    def run(self, command: str, shell: str, timeout: float, cancel: CancelToken) -> CommandOutput:
        if shell == "powershell":
            exe = self.pwsh or self.powershell
            args: Any = [exe, "-NoProfile", "-NonInteractive", "-EncodedCommand", self._encode(command)]
        else:
            args = f'cmd.exe /d /s /c "chcp 65001>nul & {command}"'
        proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.PIPE, stdin=subprocess.DEVNULL,
                                creationflags=hidden_flags(), cwd=os.path.expanduser("~"))
        return self._wait(proc, timeout, cancel)

    @staticmethod
    def _wait(proc: subprocess.Popen[bytes], timeout: float, cancel: CancelToken) -> CommandOutput:
        out: dict[str, bytes] = {}

        def reader(name: str, stream: Any) -> None:
            out[name] = stream.read()

        threads = [threading.Thread(target=reader, args=(n, s), daemon=True)
                   for n, s in (("out", proc.stdout), ("err", proc.stderr))]
        for t in threads:
            t.start()
        deadline = time.monotonic() + timeout
        timed_out = False
        while proc.poll() is None:
            if cancel.cancelled or time.monotonic() > deadline:
                timed_out = not cancel.cancelled
                _kill_tree(proc.pid)
                try:
                    proc.wait(3)
                except subprocess.TimeoutExpired:
                    pass
                break
            time.sleep(0.05)
        for t in threads:
            t.join(2)

        def dec(b: bytes) -> str:
            try:
                return b.decode("utf-8")
            except UnicodeDecodeError:
                return b.decode("cp850", errors="replace")

        return CommandOutput(proc.returncode if not timed_out else None, dec(out.get("out", b"")),
                             dec(out.get("err", b"")), timed_out)

    def run_elevated(self, command: str, timeout: float, cancel: CancelToken) -> CommandOutput:
        fd, tmp = tempfile.mkstemp(prefix="assistant-admin-", suffix=".txt")
        os.close(fd)
        q_tmp = tmp.replace("'", "''")
        q_ps = self.powershell.replace("'", "''")
        inner = self._encode(f"& {{ {command} }} *> '{q_tmp}'")
        outer = (f"try {{ $p = Start-Process -FilePath '{q_ps}' -Verb RunAs -WindowStyle Hidden "
                 f"-ArgumentList '-NoProfile','-NonInteractive','-EncodedCommand','{inner}' -Wait -PassThru; "
                 f"exit $p.ExitCode }} catch {{ Write-Error $_; exit 1223 }}")
        try:
            res = self.run(outer, "powershell", timeout, cancel)
            body = Path(tmp).read_text(encoding="utf-16", errors="replace") if Path(tmp).stat().st_size else ""
        finally:
            try:
                os.remove(tmp)
            except OSError:
                pass
        if res.code == 1223:
            return CommandOutput(1223, "", "The administrator prompt was cancelled.", False)
        return CommandOutput(res.code, body, res.stderr, res.timed_out)


def _kill_tree(pid: int) -> None:
    try:
        parent = psutil.Process(pid)
        for child in parent.children(recursive=True):
            child.kill()
        parent.kill()
    except psutil.Error:
        pass


# =============================================================================== recycle bin

class WinTrash:
    def __init__(self) -> None:
        # Only the IFileOperation implementation refuses permanent deletes; the legacy
        # SHFileOperation fallback can silently delete forever on drives without a recycle bin.
        from send2trash.win.modern import send2trash  # noqa: F401  (needs pywin32)

    def send(self, path: str) -> None:
        from send2trash.win.modern import send2trash

        try:
            send2trash(path)
        except OSError as e:
            raise ToolError(f"Couldn't move {path} to the Recycle Bin ({e}); nothing was deleted.") from None
        if os.path.exists(path):
            raise ToolError(f"{path} is still there; it may not be recyclable. Nothing was deleted permanently.")


# =============================================================================== Everything

class EverythingSearch:
    """voidtools Everything via its SDK DLL (needs Everything running)."""

    name = "Everything"

    def __init__(self, dll: Path):
        self.dll = ctypes.WinDLL(str(dll))  # type: ignore[attr-defined]
        d = self.dll
        d.Everything_SetSearchW.argtypes = [ctypes.c_wchar_p]
        d.Everything_SetMax.argtypes = [ctypes.c_uint32]
        d.Everything_SetRequestFlags.argtypes = [ctypes.c_uint32]
        d.Everything_QueryW.argtypes = [ctypes.c_int32]
        d.Everything_QueryW.restype = ctypes.c_int32
        d.Everything_GetNumResults.restype = ctypes.c_uint32
        d.Everything_GetResultFullPathNameW.argtypes = [ctypes.c_uint32, ctypes.c_wchar_p, ctypes.c_uint32]
        d.Everything_GetLastError.restype = ctypes.c_uint32
        d.Everything_IsDBLoaded.restype = ctypes.c_int32
        self._lock = threading.Lock()
        if not d.Everything_IsDBLoaded():
            raise OSError("Everything is not running (or its database isn't loaded yet)")

    def search(self, query: str, limit: int, folder: str | None, cancel: CancelToken) -> list[str]:
        q = query.replace('"', "")
        if folder:
            q = f'"{folder.rstrip(chr(92))}\\" {q}'
        with self._lock:
            d = self.dll
            d.Everything_SetSearchW(q)
            d.Everything_SetMax(limit)
            d.Everything_SetRequestFlags(0x00000004)  # EVERYTHING_REQUEST_FULL_PATH_AND_FILE_NAME
            if not d.Everything_QueryW(1):
                raise OSError(f"Everything query failed (error {d.Everything_GetLastError()})")
            buf = ctypes.create_unicode_buffer(32768)
            out = []
            for i in range(d.Everything_GetNumResults()):
                d.Everything_GetResultFullPathNameW(i, buf, len(buf))
                out.append(buf.value)
            return out


# =============================================================================== assembly

def create_windows_platform(paths: Any) -> Platform:
    p = Platform(name="windows")

    def attempt(attr: str, factory: Any, label: str) -> None:
        try:
            setattr(p, attr, factory())
        except Exception as e:
            log.warning("%s unavailable: %s", label, e)
            setattr(p, attr, Unsupported(label, f"{label} isn't available on this PC ({e})."))

    cache = Path(paths.cache_dir) if paths is not None else Path(tempfile.gettempdir())
    attempt("input", WinInput, "Mouse/keyboard control")
    inp = p.input if isinstance(p.input, WinInput) else None
    attempt("windows", lambda: WinWindows(inp.nudge if inp else None), "Window control")
    attempt("apps", lambda: WinApps(cache), "Opening apps")
    attempt("desktops", WinDesktops, "Virtual desktops")
    send_key = (lambda vk: inp._send([key_input(vk, False), key_input(vk, True)])) if inp else None
    attempt("media", lambda: WinMedia(send_key), "Media control")
    attempt("audio", WinAudio, "Volume control")
    attempt("sound", WinSound, "Playing sound files")
    attempt("display", WinDisplay, "Brightness control")
    attempt("clipboard", WinClipboard, "Clipboard")
    attempt("power", WinPower, "Power control")
    attempt("screen", WinScreen, "Screen capture")
    click = inp.click if inp else None
    attempt("uia", lambda: WinUia(click), "UI Automation")
    attempt("shell", WinShell, "Terminal")
    attempt("trash", WinTrash, "Recycle Bin")
    try:
        p.known_folders = known_folders()
    except Exception as e:
        log.warning("known folders unavailable: %s", e)
    if paths is not None:
        for dll in (Path(paths.bin_dir) / "Everything64.dll",
                    Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "Everything" / "Everything64.dll"):
            if dll.exists():
                try:
                    p.search = EverythingSearch(dll)
                    log.info("file search: Everything (%s)", dll)
                    break
                except Exception as e:
                    log.info("Everything not usable: %s", e)
    return p

