"""Fake OS backends: record what the tools asked for, never touch the real system."""

from __future__ import annotations

import itertools
import shutil
from pathlib import Path

from assistant.core.cancel import CancelToken
from assistant.tools.platform import (AppEntry, CommandOutput, DesktopInfo, OcrLine, Platform, UiElement,
                                      WindowInfo)

_hwnds = itertools.count(1000)


class FakeWindows:
    def __init__(self) -> None:
        self.windows: list[WindowInfo] = []
        self.fg: int | None = None
        self.closed: list[int] = []
        self.focused: list[int] = []
        self.killed: list[int] = []
        self.states: list[tuple[int, str]] = []

    def add(self, title: str, exe: str) -> WindowInfo:
        w = WindowInfo(hwnd=next(_hwnds), title=title, pid=next(_hwnds), exe=exe)
        self.windows.append(w)
        return w

    def list_windows(self) -> list[WindowInfo]:
        return list(self.windows)

    def foreground(self) -> WindowInfo | None:
        return next((w for w in self.windows if w.hwnd == self.fg), None)

    def focus(self, hwnd: int) -> bool:
        self.focused.append(hwnd)
        self.fg = hwnd
        return True

    def close(self, hwnd: int) -> None:
        self.closed.append(hwnd)

    def set_state(self, hwnd: int, state: str) -> None:
        self.states.append((hwnd, state))

    def rect(self, hwnd: int) -> tuple[int, int, int, int]:
        return (0, 0, 800, 600)

    def kill(self, pid: int) -> None:
        self.killed.append(pid)


class FakeDesktops:
    def __init__(self) -> None:
        self.desks = [DesktopInfo(1, "", True)]
        self.where: dict[int, int] = {}
        self.switches: list[int] = []

    @property
    def current(self) -> int:
        return next(d.number for d in self.desks if d.current)

    def list(self) -> list[DesktopInfo]:
        return [DesktopInfo(d.number, d.name, d.current) for d in self.desks]

    def switch(self, number: int) -> None:
        for d in self.desks:
            d.current = d.number == number
        self.switches.append(number)

    def create(self, name: str) -> DesktopInfo:
        d = DesktopInfo(len(self.desks) + 1, name, False)
        self.desks.append(d)
        return d

    def move_window(self, hwnd: int, number: int) -> None:
        self.where[hwnd] = number

    def window_desktop(self, hwnd: int) -> int | None:
        return self.where.get(hwnd, 1)


class FakeApps:
    def __init__(self, windows: FakeWindows, desktops: FakeDesktops) -> None:
        self.w = windows
        self.d = desktops
        self.apps = [
            AppEntry("Spotify", "SpotifyAB.SpotifyMusic_zpdnekdrzrea0!Spotify", "store", "Spotify.exe"),
            AppEntry("Word", "Microsoft.Office.WINWORD.EXE.15", "desktop", "WINWORD.EXE"),
            AppEntry("WordPad", "{GUID}\\Windows NT\\Accessories\\wordpad.exe", "desktop", "wordpad.exe"),
            AppEntry("Google Chrome", "Chrome", "desktop", "chrome.exe"),
            AppEntry("Steam", "{GUID}\\Steam\\steam.exe", "desktop", "steam.exe"),
            AppEntry("Discord", "com.squirrel.Discord.Discord", "desktop", "Discord.exe"),
            AppEntry("Notepad", "Microsoft.WindowsNotepad_8wekyb3d8bbwe!App", "store", "Notepad.exe"),
            AppEntry("Calculator", "Microsoft.WindowsCalculator_8wekyb3d8bbwe!App", "store", "CalculatorApp.exe"),
        ]
        self.launched: list[str] = []
        self.opened: list[str] = []
        self.spawn_window = True

    def list_apps(self, refresh: bool = False) -> list[AppEntry]:
        return list(self.apps)

    def launch(self, app: AppEntry, arguments: str = "") -> None:
        self.launched.append(app.name)
        if self.spawn_window:
            w = self.w.add(app.name, app.exe)
            self.w.fg = w.hwnd  # new apps steal focus, like on Windows

    def open_target(self, target: str) -> None:
        self.opened.append(target)
        if target.startswith("spotify:"):
            # Spotify activates its window -> Windows jumps to the desktop it lives on
            sp = next((w for w in self.w.windows if w.exe == "Spotify.exe"), None)
            if sp is not None:
                self.d.switch(self.d.where.get(sp.hwnd, 1))
                self.w.fg = sp.hwnd


class FakeMedia:
    def __init__(self) -> None:
        self.commands: list[str] = []
        self.playing: dict | None = {"title": "Song", "artist": "Band", "app": "Spotify.exe", "status": "playing"}

    def command(self, action: str) -> bool:
        self.commands.append(action)
        return True

    def status(self) -> dict | None:
        return self.playing


class FakeAudio:
    def __init__(self) -> None:
        self.volume, self.muted = 50, False

    def get_volume(self) -> int:
        return self.volume

    def set_volume(self, percent: int) -> None:
        self.volume = percent

    def get_mute(self) -> bool:
        return self.muted

    def set_mute(self, mute: bool) -> None:
        self.muted = mute


class FakeDisplay:
    def __init__(self) -> None:
        self.level = 70

    def get_brightness(self) -> int | None:
        return self.level

    def set_brightness(self, percent: int) -> None:
        self.level = percent


class FakeClipboard:
    def __init__(self) -> None:
        self.text = ""

    def get_text(self) -> str:
        return self.text

    def set_text(self, text: str) -> None:
        self.text = text


class FakePower:
    def __init__(self) -> None:
        self.calls: list[str] = []

    def __getattr__(self, name: str):  # lock/sleep/shutdown/restart/sign_out
        if name.startswith("_"):
            raise AttributeError(name)
        return lambda: self.calls.append(name)


class FakeInput:
    def __init__(self) -> None:
        self.events: list[tuple] = []

    def cursor_pos(self) -> tuple[int, int]:
        return (10, 10)

    def screen_rect(self) -> tuple[int, int, int, int]:
        return (0, 0, 1920, 1080)

    def move(self, x: int, y: int) -> None:
        self.events.append(("move", x, y))

    def click(self, x, y, button="left", count=1) -> None:
        self.events.append(("click", x, y, button, count))

    def drag(self, x1, y1, x2, y2, button="left") -> None:
        self.events.append(("drag", x1, y1, x2, y2))

    def scroll(self, clicks, x=None, y=None) -> None:
        self.events.append(("scroll", clicks, x, y))

    def type_text(self, text: str, cancel: CancelToken) -> None:
        cancel.check()
        self.events.append(("type", text))

    def hotkey(self, keys: list[str]) -> None:
        self.events.append(("hotkey", tuple(keys)))

    def release_all(self) -> None:
        self.events.append(("release_all",))


class FakeScreen:
    def __init__(self) -> None:
        self.lines = [OcrLine("File", 10, 10, 30, 12), OcrLine("Save as PDF", 400, 300, 90, 14)]

    def screenshot_png(self, region=None) -> tuple[bytes, int, int]:
        return b"\x89PNG fake", 1920, 1080

    def ocr(self, png: bytes, language: str) -> list[OcrLine]:
        return list(self.lines)


class FakeUia:
    def __init__(self) -> None:
        self.by_hwnd: dict[int | None, list[UiElement]] = {}
        self.activated: list[str] = []

    def elements(self, hwnd: int | None, limit: int, cancel: CancelToken) -> list[UiElement]:
        return list(self.by_hwnd.get(hwnd, []))[:limit]

    def activate(self, element: UiElement, hwnd: int | None) -> str:
        self.activated.append(element.name)
        return "invoke"


class FakeShell:
    def __init__(self) -> None:
        self.commands: list[tuple[str, str]] = []
        self.output = CommandOutput(0, "fake output", "")

    def run(self, command: str, shell: str, timeout: float, cancel: CancelToken) -> CommandOutput:
        self.commands.append((shell, command))
        return self.output

    def run_elevated(self, command: str, timeout: float, cancel: CancelToken) -> CommandOutput:
        self.commands.append(("admin", command))
        return self.output


class FakeTrash:
    """Moves files into a folder instead of deleting - lets tests prove nothing is lost."""

    def __init__(self, folder: Path) -> None:
        self.folder = folder
        self.items: list[str] = []

    def send(self, path: str) -> None:
        self.folder.mkdir(parents=True, exist_ok=True)
        dest = self.folder / f"{len(self.items)}-{Path(path).name}"
        shutil.move(path, dest)
        self.items.append(path)


class FakeSearch:
    name = "fake search"

    def __init__(self) -> None:
        self.results: list[str] = []

    def search(self, query, limit, folder, cancel):
        return [r for r in self.results if query.lower() in Path(r).name.lower()][:limit]


def fake_platform(home: Path) -> Platform:
    windows, desktops = FakeWindows(), FakeDesktops()
    p = Platform(
        apps=FakeApps(windows, desktops), windows=windows, desktops=desktops, media=FakeMedia(),
        audio=FakeAudio(), display=FakeDisplay(), clipboard=FakeClipboard(), power=FakePower(),
        input=FakeInput(), screen=FakeScreen(), uia=FakeUia(), shell=FakeShell(),
        trash=FakeTrash(home / ".trash"), search=FakeSearch(), name="fake",
    )
    for name in ("Desktop", "Documents", "Downloads", "Pictures", "Music", "Videos"):
        (home / name).mkdir(parents=True, exist_ok=True)
        p.known_folders[name.lower()] = str(home / name)
    p.known_folders["home"] = str(home)
    return p
