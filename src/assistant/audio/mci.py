"""Playing a sound file through Windows' MCI player (winmm): MP3/WAV/WMA, no window, no extra packages.

``play_session`` is the whole playing logic - repeat while it should go on, a minimum time, "until the routine is
done" with a short fade-out, stop/cancel - and talks to MCI only through ``cmd(str) -> str``, so it is tested
without Windows (tests feed it a fake player).
"""

from __future__ import annotations

import threading
import time
from collections.abc import Callable

FADE_STEPS = 10           # fade-out: 10 volume steps of 0.1 s


def play_session(cmd: Callable[[str], str], alias: str, path: str, *, stop: threading.Event,
                 cancelled: Callable[[], bool] | None = None, until: Callable[[], bool] | None = None,
                 min_s: float = 0.0, volume: float = 1.0, speaking: Callable[[], bool] | None = None,
                 duck_level: Callable[[], float] | None = None, max_s: float = 900.0, poll: float = 0.1,
                 clock: Callable[[], float] = time.monotonic) -> None:
    """Play ``path`` once - again from the start while it should go on - then close it.

    * ``min_s``: play at least this long (a shorter sound starts again).
    * ``until``: keep playing (repeating) until it returns True, then fade out - unless ``min_s`` isn't over yet.
    * ``stop`` / ``cancelled``: end at once ("stop", panic hotkey, the routine was cancelled).
    * ``volume`` (0-1), and lower (``duck_level``) while ``speaking()``: the assistant's voice stays clear.
    """
    cmd(f'open "{path}" type mpegvideo alias {alias}')
    level = 1000                          # MCI volume 0..1000

    def set_level(want: float) -> None:
        nonlocal level
        v = int(max(0, min(1000, want)))
        if v != level:
            try:
                cmd(f"setaudio {alias} volume to {v}")
                level = v
            except OSError:               # some files/devices have no volume control
                pass

    def wanted() -> float:
        duck = speaking is not None and duck_level is not None and speaking()
        return 1000 * volume * (duck_level() if duck and duck_level is not None else 1.0)

    try:
        set_level(wanted())
        cmd(f"play {alias}")
        t0 = t_play = clock()
        seen = False                      # has this round reported "playing" yet?
        while not stop.wait(poll):
            if cancelled is not None and cancelled():
                return
            elapsed = clock() - t0
            if elapsed > max_s:
                return
            set_level(wanted())
            mode = cmd(f"status {alias} mode")          # playing | stopped | not ready | seeking ...
            seen = seen or mode == "playing"
            ended = mode == "stopped" and (seen or clock() - t_play > 1.5)
            done = until is not None and until()
            if done and elapsed >= min_s and not ended:
                _fade(cmd, alias, stop, level)          # the routine has finished: end the music softly
                return
            if ended:
                if seen and (elapsed < min_s or (until is not None and not done)):
                    cmd(f"play {alias} from 0")         # shorter than wanted: once more from the start
                    t_play, seen = clock(), False
                    continue
                return
    finally:
        try:
            cmd(f"close {alias}")
        except OSError:
            pass


def _fade(cmd: Callable[[str], str], alias: str, stop: threading.Event, start: int = 1000) -> None:
    for i in range(FADE_STEPS - 1, -1, -1):
        try:
            cmd(f"setaudio {alias} volume to {i * start // FADE_STEPS}")
        except OSError:           # some files/devices have no volume control: just stop
            return
        if stop.wait(0.1):
            return


__all__ = ["play_session"]
