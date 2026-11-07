"""OS backends used by the tools.

Tools contain the logic (matching, safety checks, wording) and talk to the OS
only through these small interfaces. ``create_platform()`` returns the
Windows implementations from ``assistant.win``; tests plug in fakes; on other
OSes every backend raises a friendly ToolError.
"""

from __future__ import annotations

import logging
import sys
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any, Protocol

from ..core.cancel import CancelToken

log = logging.getLogger(__name__)


# ----------------------------------------------------------------------------- data types

@dataclass
class AppEntry:
    name: str                 # "Spotify"
    app_id: str               # AUMID or path, as reported by Windows
    kind: str = "desktop"     # "desktop" | "store" | "url"
    exe: str = ""             # process image name if known ("spotify.exe")


@dataclass
class WindowInfo:
    hwnd: int
    title: str
    pid: int
    exe: str = ""             # "spotify.exe"
    minimized: bool = False
    cloaked: bool = False     # e.g. on another virtual desktop
    desktop: int | None = None


@dataclass
class DesktopInfo:
    number: int               # 1-based, as in Task View
    name: str
    current: bool = False


@dataclass
class OcrLine:
    text: str
    x: int
    y: int
    w: int
    h: int


@dataclass
class UiElement:
    name: str
    control_type: str
    x: int
    y: int
    w: int
    h: int
    enabled: bool = True
    automation_id: str = ""

    @property
    def center(self) -> tuple[int, int]:
        return self.x + self.w // 2, self.y + self.h // 2


@dataclass
class CommandOutput:
    code: int | None          # None = timed out / killed
    stdout: str
    stderr: str
    timed_out: bool = False


# ----------------------------------------------------------------------------- interfaces

class AppsBackend(Protocol):
    def list_apps(self, refresh: bool = False) -> list[AppEntry]: ...
    def launch(self, app: AppEntry, arguments: str = "") -> None: ...
    def open_target(self, target: str) -> None: ...      # file, folder, URL, shell: URI


class WindowsBackend(Protocol):
    def list_windows(self) -> list[WindowInfo]: ...
    def foreground(self) -> WindowInfo | None: ...
    def focus(self, hwnd: int) -> bool: ...
    def close(self, hwnd: int) -> None: ...
    def set_state(self, hwnd: int, state: str) -> None: ...   # minimize | maximize | restore
    def rect(self, hwnd: int) -> tuple[int, int, int, int]: ...  # x, y, w, h (physical px)
    def kill(self, pid: int) -> None: ...


class DesktopsBackend(Protocol):
    def list(self) -> list[DesktopInfo]: ...
    def switch(self, number: int) -> None: ...
    def create(self, name: str) -> DesktopInfo: ...
    def move_window(self, hwnd: int, number: int) -> None: ...
    def window_desktop(self, hwnd: int) -> int | None: ...


class MediaBackend(Protocol):
    def command(self, action: str) -> bool: ...          # play|pause|toggle|next|previous|stop
    def status(self) -> dict[str, Any] | None: ...       # {"title","artist","app","status"}


class AudioBackend(Protocol):
    def get_volume(self) -> int: ...
    def set_volume(self, percent: int) -> None: ...
    def get_mute(self) -> bool: ...
    def set_mute(self, mute: bool) -> None: ...


class SoundBackend(Protocol):
    def play(self, path: str, wait: bool, cancelled: Callable[[], bool] | None = None, *,
             until: Callable[[], bool] | None = None, min_s: float = 0.0) -> None: ...
    def stop_all(self) -> None: ...


class DisplayBackend(Protocol):
    def get_brightness(self) -> int | None: ...
    def set_brightness(self, percent: int) -> None: ...


class ClipboardBackend(Protocol):
    def get_text(self) -> str: ...
    def set_text(self, text: str) -> None: ...


class PowerBackend(Protocol):
    def lock(self) -> None: ...
    def sleep(self) -> None: ...
    def shutdown(self) -> None: ...
    def restart(self) -> None: ...
    def sign_out(self) -> None: ...


class InputBackend(Protocol):
    def cursor_pos(self) -> tuple[int, int]: ...
    def screen_rect(self) -> tuple[int, int, int, int]: ...  # virtual screen x, y, w, h (physical px)
    def move(self, x: int, y: int) -> None: ...
    def click(self, x: int | None, y: int | None, button: str = "left", count: int = 1) -> None: ...
    def drag(self, x1: int, y1: int, x2: int, y2: int, button: str = "left") -> None: ...
    def scroll(self, clicks: int, x: int | None = None, y: int | None = None) -> None: ...
    def type_text(self, text: str, cancel: CancelToken) -> None: ...
    def hotkey(self, keys: list[str]) -> None: ...
    def release_all(self) -> None: ...


class ScreenBackend(Protocol):
    def screenshot_png(self, region: tuple[int, int, int, int] | None = None) -> tuple[bytes, int, int]: ...
    def ocr(self, png: bytes, language: str) -> list[OcrLine]: ...


class UiaBackend(Protocol):
    def elements(self, hwnd: int | None, limit: int, cancel: CancelToken) -> list[UiElement]: ...
    def activate(self, element: UiElement, hwnd: int | None) -> str: ...  # returns how it was activated


class ShellBackend(Protocol):
    def run(self, command: str, shell: str, timeout: float, cancel: CancelToken) -> CommandOutput: ...
    def run_elevated(self, command: str, timeout: float, cancel: CancelToken) -> CommandOutput: ...


class TrashBackend(Protocol):
    def send(self, path: str) -> None: ...


class SearchBackend(Protocol):
    name: str
    def search(self, query: str, limit: int, folder: str | None, cancel: CancelToken) -> list[str]: ...


# ----------------------------------------------------------------------------- container

class Unsupported:
    """Placeholder backend: every call explains that the feature needs Windows."""

    def __init__(self, what: str, reason: str = ""):
        self._what = what
        self._reason = reason or f"{what} is only available on Windows"

    def __getattr__(self, name: str) -> Any:
        if name.startswith("__"):
            raise AttributeError(name)
        from .base import ToolError

        def fail(*_a: Any, **_k: Any) -> Any:
            raise ToolError(self._reason)

        return fail


@dataclass
class Platform:
    apps: AppsBackend = field(default_factory=lambda: Unsupported("Opening apps"))  # type: ignore[assignment]
    windows: WindowsBackend = field(default_factory=lambda: Unsupported("Window control"))  # type: ignore[assignment]
    desktops: DesktopsBackend = field(default_factory=lambda: Unsupported("Virtual desktops"))  # type: ignore[assignment]
    media: MediaBackend = field(default_factory=lambda: Unsupported("Media control"))  # type: ignore[assignment]
    audio: AudioBackend = field(default_factory=lambda: Unsupported("Volume control"))  # type: ignore[assignment]
    sound: SoundBackend = field(default_factory=lambda: Unsupported("Playing sound files"))  # type: ignore[assignment]
    display: DisplayBackend = field(default_factory=lambda: Unsupported("Brightness control"))  # type: ignore[assignment]
    clipboard: ClipboardBackend = field(default_factory=lambda: Unsupported("Clipboard"))  # type: ignore[assignment]
    power: PowerBackend = field(default_factory=lambda: Unsupported("Power control"))  # type: ignore[assignment]
    input: InputBackend = field(default_factory=lambda: Unsupported("Mouse/keyboard control"))  # type: ignore[assignment]
    screen: ScreenBackend = field(default_factory=lambda: Unsupported("Screen capture"))  # type: ignore[assignment]
    uia: UiaBackend = field(default_factory=lambda: Unsupported("UI Automation"))  # type: ignore[assignment]
    shell: ShellBackend = field(default_factory=lambda: Unsupported("The terminal tool"))  # type: ignore[assignment]
    trash: TrashBackend = field(default_factory=lambda: Unsupported("Deleting to the Recycle Bin"))  # type: ignore[assignment]
    search: SearchBackend | None = None
    known_folders: dict[str, str] = field(default_factory=dict)  # "downloads" -> path
    name: str = "unsupported"


def create_platform(paths: Any = None) -> Platform:
    """The real backends for this OS (Windows), each one loaded defensively."""
    if sys.platform != "win32":
        from ..audio.sound import SoundEngine

        return Platform(sound=SoundEngine())     # (sound files play on any OS; the rest needs Windows)
    from ..win.backends import create_windows_platform

    return create_windows_platform(paths)
