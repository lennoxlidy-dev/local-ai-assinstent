"""Automatic game mode: while a fullscreen game runs, the language model leaves the GPU.

A small watcher thread looks at the foreground window every few seconds (a handful of
Win32 calls - no hooks, no screenshots). It is a *game* when the window covers its monitor and

* it runs in exclusive fullscreen (Windows reports a D3D fullscreen app), or
* the program is in your "always a game" list, or lives in a game library
  (Steam, Epic, GOG, Xbox/Microsoft Store games, Riot, Battle.net, EA, Ubisoft, Rockstar), or
* it loads a 3D API (Direct3D 11/12, Vulkan, OpenGL) and isn't a known non-game
  (browsers, video players, Office, presentations, remote desktop ...).

Game mode starts after ~6 s in a game and ends ~20 s after it (so alt-tabbing doesn't
reload 6 GB). Turning game mode on/off in the tray always wins over the automatic choice.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from .context import AppContext
from .events import Event, Topic

log = logging.getLogger(__name__)

GAME_DIRS = ("\\steamapps\\common\\", "\\epic games\\", "\\gog galaxy\\games\\", "\\gog games\\", "\\xboxgames\\",
             "\\modifiablewindowsapps\\", "\\riot games\\", "\\battle.net\\", "\\blizzard\\", "\\ea games\\",
             "\\electronic arts\\", "\\origin games\\", "\\ubisoft game launcher\\games\\", "\\rockstar games\\",
             "\\games\\", "\\spiele\\")
NON_GAMES = {
    "chrome.exe", "msedge.exe", "firefox.exe", "brave.exe", "opera.exe", "opera_gx.exe", "vivaldi.exe", "arc.exe",
    "zen.exe", "librewolf.exe", "chromium.exe", "iexplore.exe", "vlc.exe", "mpv.exe", "mpc-hc64.exe", "mpc-hc.exe",
    "mpc-be64.exe", "potplayermini64.exe", "wmplayer.exe", "microsoft.media.player.exe", "video.ui.exe",
    "applicationframehost.exe", "powerpnt.exe", "winword.exe", "excel.exe", "outlook.exe", "onenote.exe",
    "teams.exe", "ms-teams.exe", "zoom.exe", "slack.exe", "discord.exe", "spotify.exe", "obs64.exe", "explorer.exe",
    "mstsc.exe", "msrdc.exe", "vmware.exe", "vmplayer.exe", "virtualboxvm.exe", "code.exe", "devenv.exe",
    "windowsterminal.exe", "photos.exe", "photoshop.exe", "netflix.exe", "acrord32.exe", "acrobat.exe",
    "sumatrapdf.exe", "steam.exe", "steamwebhelper.exe", "epicgameslauncher.exe", "galaxyclient.exe",
}


@dataclass
class ForegroundInfo:
    fullscreen: bool               # the foreground window covers its whole monitor
    exclusive: bool = False        # Windows reports exclusive (D3D) fullscreen
    exe: str = ""                  # full path of the program ("" if not readable)
    title: str = ""
    pid: int = 0
    uses_3d: bool | None = None    # loads a 3D API (None: couldn't tell)

    @property
    def name(self) -> str:
        return self.exe.replace("/", "\\").rsplit("\\", 1)[-1].lower()


def classify(info: ForegroundInfo | None, games: list[str], not_games: list[str]) -> bool:
    """Is this foreground window a running game?"""
    if info is None or not info.fullscreen:
        return False
    name = info.name
    if name and name in {g.strip().lower() for g in games if g.strip()}:
        return True
    if name in NON_GAMES or name in {g.strip().lower() for g in not_games if g.strip()}:
        return False
    if info.exclusive:
        return True
    path = info.exe.replace("/", "\\").lower()
    if any(d in path for d in GAME_DIRS):
        return True
    return info.uses_3d is True


class GameModeWatcher:
    POLL_S = 3.0
    ENTER_POLLS = 2          # ~6 s in a fullscreen game
    LEAVE_S = 20.0           # alt-tab grace period

    def __init__(self, ctx: AppContext, probe: Callable[[], ForegroundInfo | None] | None = None,
                 clock: Callable[[], float] = time.monotonic):
        self.ctx = ctx
        if probe is None:
            from ..win.fullscreen import foreground_info

            probe = foreground_info
        self.probe = probe
        self.clock = clock
        self.auto_on = False          # game mode is on because *we* turned it on
        self.manual = False           # the user switched it on themselves: never turn it off
        self._suppressed_pid = 0      # the user switched it off during this game: leave it alone
        self._hits = 0
        self._last_game = 0.0
        self._game_pid = 0
        self._setting = False
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._unsub = ctx.bus.subscribe(Topic.STATE_CHANGED, self._on_state)

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        if self._thread is not None:
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._run, name="game-mode", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(2)
            self._thread = None
        if self.auto_on:
            self._set(False, "")

    def close(self) -> None:
        self.stop()
        self._unsub()

    def _run(self) -> None:
        while not self._stop.wait(self.POLL_S):
            try:
                self.check()
            except Exception:
                log.exception("game mode check failed")

    # ------------------------------------------------------------------ decision
    def check(self) -> None:
        p = self.ctx.config.data.performance
        info = self.probe()
        game = classify(info, list(p.games), list(p.not_games))
        now = self.clock()
        if game and info is not None:
            if info.pid != self._game_pid:
                self._game_pid, self._hits = info.pid, 0
            self._hits += 1
            self._last_game = now
            if self._suppressed_pid and self._suppressed_pid != info.pid:
                self._suppressed_pid = 0          # a different game: automatic again
            if (not self.ctx.state.game_mode and not self._suppressed_pid and self._hits >= self.ENTER_POLLS):
                log.info("game detected (%s%s) -> game mode", info.name or info.title,
                         ", exclusive fullscreen" if info.exclusive else "")
                self._set(True, info.name or info.title or "game", exclusive=info.exclusive)
            elif self.auto_on and self.ctx.state.game_exclusive != info.exclusive:
                self.ctx.state.update(game_exclusive=info.exclusive)
            return
        self._hits = 0
        if self.auto_on and not self.manual and now - self._last_game >= self.LEAVE_S:
            log.info("game closed -> leaving game mode")
            self._set(False, "")
        if self._suppressed_pid and now - self._last_game >= self.LEAVE_S:
            self._suppressed_pid = 0

    def _set(self, on: bool, reason: str, exclusive: bool = False) -> None:
        self._setting = True
        try:
            self.auto_on = on
            self.ctx.state.update(game_mode=on, game_reason=reason if on else "", game_exclusive=exclusive and on)
        finally:
            self._setting = False

    def _on_state(self, e: Event) -> None:
        changed = e.data.get("changed", {})
        if "game_mode" not in changed or self._setting:
            return
        if changed["game_mode"]:                # switched on in the tray
            self.manual = True
            self.auto_on = False
            if not self.ctx.state.game_reason:
                self.ctx.state.update(game_reason="manual")
        else:                                   # switched off in the tray
            if self.auto_on and self._game_pid:
                self._suppressed_pid = self._game_pid
            self.manual = self.auto_on = False
            self.ctx.state.update(game_reason="", game_exclusive=False)


def describe(state: Any) -> str:
    return "" if not state.game_mode else ("" if state.game_reason in ("", "manual") else state.game_reason)


__all__ = ["ForegroundInfo", "GameModeWatcher", "classify", "describe"]
