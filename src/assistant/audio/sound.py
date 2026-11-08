"""Routine sounds played by the assistant itself: MP3, WAV, FLAC and OGG are decoded with soundfile (libsndfile
1.2 - its Windows build includes the mpg123 MP3 decoder) and played on the same output device as the assistant's
voice, resampled to the voice's 44.1 kHz. Other formats (WMA, M4A, AAC) go to the ``fallback`` backend (Windows'
MCI player).

Each file is decoded with ONE read call and played from memory: libsndfile 1.2's MP3 decoder garbles VBR files
(the most common kind) wherever a read is split - silence or noise at every boundary, with 2 KB as with 30 s pieces
(checked against ffmpeg) - while a single read is exact. 16-bit samples, at most the first ``MAX_DECODE_S``.

``play_track`` holds the playing logic - a set playing time, "until the routine is done" with a fade-out, stop -
over a reader and a writer, so it is tested with real MP3 files and a fake output device.
"""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path
from typing import Any

import numpy as np

from ..tools.base import ToolError
from .player import OUT_RATE

log = logging.getLogger(__name__)

DECODED_EXT = (".mp3", ".wav", ".flac", ".ogg")
BLOCK = 2048                 # frames per write (~46 ms)
FADE_S = 1.0
MAX_DECODE_S = 300.0         # a routine sound: the first 5 minutes at most (~50 MB as 16-bit stereo)


def play_track(read: Callable[[int], np.ndarray], rewind: Callable[[], None], write: Callable[[np.ndarray], object],
               rate: int, *, stop: threading.Event, cancelled: Callable[[], bool] | None = None,
               until: Callable[[], bool] | None = None, seconds: float = 0.0, max_s: float = 900.0) -> float:
    """Write the sound block by block; start again from the beginning while it should go on. Returns seconds played.

    * without ``until``: the sound once - or, with ``seconds``, exactly that long: a longer sound fades out so that it
      ends right then, a shorter one starts again (and its last round fades out the same way).
    * ``until``: keep playing (repeating) until it returns True - but at least ``seconds`` - then fade out over
      ``FADE_S``. (A routine that is done early: the sound ends at ``seconds``.)
    * ``stop`` / ``cancelled``: end at once.
    """
    end = int(rate * seconds) if seconds > 0 else 0          # frames; 0 = no set time
    played = since_rewind = 0
    fade_len = max(1, int(rate * FADE_S))
    if until is None and end:
        fade_len = max(1, min(fade_len, end // 4))          # (a short time: a shorter fade)
    fade_left: int | None = None
    fixed = until is None and end > 0                        # "play for … s"
    while not stop.is_set() and not (cancelled is not None and cancelled()):
        if played >= rate * max_s or (fixed and played >= end):
            break
        if fade_left is None and played >= end - fade_len:
            if fixed:                                       # the time is up: fade out, to end exactly at ``seconds``
                fade_len = fade_left = end - played
            elif until is not None and until():             # the routine is done (and the minimum nearly over)
                fade_left = fade_len
        # (a set time: blocks end exactly where the fade starts, and the fade exactly at ``seconds``)
        block = read(min(BLOCK, (end if fade_left is not None else end - fade_len) - played) if fixed else BLOCK)
        if not len(block):                                  # end of the file
            # again from the start while the time isn't over, a fade-out still runs, or the routine isn't done
            more = played < end or fade_left is not None or (until is not None and not until())
            if more and since_rewind:                       # (an empty file is never repeated)
                rewind()
                since_rewind = 0
                continue
            break
        since_rewind += len(block)
        if fade_left is not None:
            block = block[:fade_left]                       # (the fade's last frame is the sound's last)
            gain = np.clip((fade_left - np.arange(len(block))) / fade_len, 0.0, 1.0).astype(np.float32)
            block = block * (gain[:, None] if block.ndim == 2 else gain)
            fade_left -= len(block)
        write(np.ascontiguousarray(block, dtype=np.float32))
        played += len(block)
        if fade_left is not None and fade_left <= 0:
            break
    return played / rate


def how_long(until_done: bool, seconds: float) -> str:
    """For the log and the step's result: "once", "for 60 s", "until the routine is done (at least 60 s)"."""
    if until_done:
        return "until the routine is done" + (f" (at least {seconds:g} s)" if seconds > 0 else "")
    return f"for {seconds:g} s" if seconds > 0 else "once"


class Ducker:
    """Gain for a routine sound: ``volume``, lowered smoothly to ``level()`` while ``speaking()`` - the assistant's
    voice stays clear over music. Down in 0.15 s, back up in 0.6 s, ramped per sample (no clicks)."""

    DOWN_S, UP_S = 0.15, 0.6

    def __init__(self, speaking: Callable[[], bool], level: Callable[[], float], rate: int, volume: float = 1.0):
        self.speaking, self.level, self.rate, self.volume = speaking, level, rate, volume
        self.g = 1.0

    def __call__(self, block: np.ndarray) -> np.ndarray:
        target = min(1.0, max(0.0, self.level())) if self.speaking() else 1.0
        if self.g == target:
            return block if self.g * self.volume == 1.0 else block * np.float32(self.g * self.volume)
        n = len(block)
        step = n / (self.rate * (self.DOWN_S if target < self.g else self.UP_S))
        new = max(target, self.g - step) if target < self.g else min(target, self.g + step)
        ramp = (np.linspace(self.g, new, n, endpoint=False) * self.volume).astype(np.float32)
        self.g = new
        return block * (ramp[:, None] if block.ndim == 2 else ramp)


class SoundEngine:
    """The ``platform.sound`` backend: decodes and plays itself; other formats go to ``fallback``.

    The app fills in ``device`` (the voice's output device), ``speaking`` (is the assistant talking?) and
    ``duck_level`` (how loud sounds stay meanwhile, Settings → Voice)."""

    def __init__(self, fallback: Any = None, device: Callable[[], str] = lambda: ""):
        self.fallback = fallback
        self.device = device                     # the voice output device (Settings → Voice)
        self.speaking: Callable[[], bool] = lambda: False
        self.duck_level: Callable[[], float] = lambda: 0.25
        self._lock = threading.Lock()
        self._stops: set[threading.Event] = set()

    def play(self, path: str, wait: bool, cancelled: Callable[[], bool] | None = None, *,
             until: Callable[[], bool] | None = None, seconds: float = 0.0, volume: float = 1.0) -> None:
        """``seconds``: how long it plays (a longer sound fades out then, a shorter one repeats); with ``until``:
        until the routine is done, but at least that long. See ``play_track``."""
        name = Path(path).name
        fallback = self._fallback_kw(until, seconds, volume)
        if Path(path).suffix.lower() not in DECODED_EXT:
            if self.fallback is None:
                raise ToolError(f"Can't play {name}: use an MP3, WAV, FLAC or OGG file.")
            self.fallback.play(path, wait, cancelled, **fallback)
            return
        import soundfile as sf

        try:
            f = sf.SoundFile(path)               # opened here, so a broken file is reported to the routine at once
        except Exception as e:
            if self.fallback is not None:        # e.g. an MP3 variant libsndfile can't read: let Windows try
                log.info("soundfile can't read %s (%s); using the fallback player", name, e)
                self.fallback.play(path, wait, cancelled, **fallback)
                return
            raise ToolError(f"Can't play {name}: {e}") from None
        stop, done, errors = threading.Event(), threading.Event(), []
        with self._lock:
            self._stops.add(stop)

        def run() -> None:
            try:
                with f:
                    self._play_file(f, stop, cancelled, until, seconds, volume)
            except Exception as e:
                log.warning("sound %s failed: %s", name, e)
                errors.append(e)
            finally:
                with self._lock:
                    self._stops.discard(stop)
                done.set()

        threading.Thread(target=run, name="sound", daemon=True).start()
        done.wait(None if wait else 0.3)         # (not waiting: still report a sound that can't start)
        if errors:
            raise ToolError(f"Can't play {name}: {errors[0]}")

    def _fallback_kw(self, until: Callable[[], bool] | None, seconds: float, volume: float) -> dict[str, Any]:
        return {"until": until, "seconds": seconds, "volume": volume, "speaking": lambda: self.speaking(),
                "duck_level": lambda: self.duck_level()}

    def _play_file(self, f: Any, stop: threading.Event, cancelled: Callable[[], bool] | None,
                   until: Callable[[], bool] | None, seconds: float, volume: float = 1.0) -> None:
        import sounddevice as sd

        from .sources import resolve_device

        channels = min(f.channels, 2)
        file_s = f.frames / f.samplerate if f.frames > 0 else 0.0
        # (a set time shorter than the sound: only that much is needed)
        cap = int(f.samplerate * (min(MAX_DECODE_S, seconds + 1) if until is None and seconds > 0 else MAX_DECODE_S))
        # ONE read call (see the module docstring): split reads garble VBR MP3s
        data = f.read(-1 if 0 <= f.frames <= cap else cap, dtype="int16", always_2d=True)[:, :channels]
        if f.samplerate != OUT_RATE and len(data):     # the voice's rate: known to work on this output device
            import soxr

            data = soxr.resample(data, f.samplerate, OUT_RATE)
        data = np.ascontiguousarray(np.asarray(data).reshape(-1, channels))
        pos = 0

        def read(n: int) -> np.ndarray:
            nonlocal pos
            block = data[pos:pos + n]
            pos += len(block)
            return block.astype(np.float32) / 32768.0

        def rewind() -> None:
            nonlocal pos
            pos = 0

        # (looked up on every block: the voice may start - and set ``speaking`` - while a sound plays)
        duck = Ducker(lambda: self.speaking(), lambda: self.duck_level(), OUT_RATE, volume)
        stream = sd.OutputStream(samplerate=OUT_RATE, channels=channels, dtype="float32",
                                 device=resolve_device(self.device(), "output"), blocksize=BLOCK)
        stream.start()
        try:
            played = play_track(read, rewind, lambda block: stream.write(duck(block)), OUT_RATE, stop=stop,
                                cancelled=cancelled, until=until, seconds=seconds)
            log.info("sound %s: %.1f s long, %s -> played %.1f s%s", Path(str(getattr(f, "name", "?"))).name,
                     file_s, how_long(until is not None, seconds), played, " - stopped" if stop.is_set() else "")
        finally:
            if stop.is_set() or (cancelled is not None and cancelled()):
                stream.abort()                   # "stop": silence now
            else:
                stream.stop()                    # let the last block play out
            stream.close()

    def stop_all(self) -> None:
        with self._lock:
            for stop in self._stops:
                stop.set()
        if self.fallback is not None:
            try:
                self.fallback.stop_all()
            except Exception:
                pass


__all__ = ["DECODED_EXT", "Ducker", "SoundEngine", "how_long", "play_track"]
