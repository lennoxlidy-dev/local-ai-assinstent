"""Routine sounds played by the assistant itself: MP3, WAV, FLAC and OGG are decoded with soundfile (libsndfile
1.2 - its Windows build includes the mpg123 MP3 decoder) and played on the same output device as the assistant's
voice, resampled to the voice's 44.1 kHz. Other formats (WMA, M4A, AAC) go to the ``fallback`` backend (Windows'
MCI player).

Each file is decoded with ONE read call and played from memory: libsndfile 1.2's MP3 decoder garbles VBR files
(the most common kind) wherever a read is split - silence or noise at every boundary, with 2 KB as with 30 s pieces
(checked against ffmpeg) - while a single read is exact. 16-bit samples, at most the first ``MAX_DECODE_S``.

``play_track`` holds the playing logic - repeat, minimum time, "until the routine is done" with a fade-out, stop -
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
               until: Callable[[], bool] | None = None, min_s: float = 0.0, max_s: float = 900.0) -> float:
    """Write the sound block by block; start again from the beginning while it should go on. Returns seconds played.

    * ``min_s``: at least this long (a shorter sound repeats).
    * ``until``: keep repeating until it returns True, then fade out over ``FADE_S`` (not before ``min_s``).
    * ``stop`` / ``cancelled``: end at once.
    """
    played = since_rewind = 0
    fade_total = max(1, int(rate * FADE_S))
    fade_left: int | None = None
    while not stop.is_set() and not (cancelled is not None and cancelled()):
        t = played / rate
        if t >= max_s:
            break
        if fade_left is None and until is not None and t >= min_s and until():
            fade_left = fade_total                          # the routine is done: fade out now
        block = read(BLOCK)
        if not len(block):                                  # end of the file
            # go on while the minimum isn't reached, the routine isn't done, or a fade-out is still running
            more = t < min_s or (until is not None and (fade_left is not None or not until()))
            if more and since_rewind:                       # (an empty file is never repeated)
                rewind()
                since_rewind = 0
                continue
            break
        since_rewind += len(block)
        if fade_left is not None:
            gain = np.clip((fade_left - np.arange(len(block))) / fade_total, 0.0, 1.0).astype(np.float32)
            block = block * (gain[:, None] if block.ndim == 2 else gain)
            fade_left -= len(block)
        write(np.ascontiguousarray(block, dtype=np.float32))
        played += len(block)
        if fade_left is not None and fade_left <= 0:
            break
    return played / rate


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
             until: Callable[[], bool] | None = None, min_s: float = 0.0, volume: float = 1.0) -> None:
        name = Path(path).name
        fallback = self._fallback_kw(until, min_s, volume)
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
                    self._play_file(f, stop, cancelled, until, min_s, volume)
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

    def _fallback_kw(self, until: Callable[[], bool] | None, min_s: float, volume: float) -> dict[str, Any]:
        return {"until": until, "min_s": min_s, "volume": volume, "speaking": lambda: self.speaking(),
                "duck_level": lambda: self.duck_level()}

    def _play_file(self, f: Any, stop: threading.Event, cancelled: Callable[[], bool] | None,
                   until: Callable[[], bool] | None, min_s: float, volume: float = 1.0) -> None:
        import sounddevice as sd

        from .sources import resolve_device

        channels = min(f.channels, 2)
        cap = int(f.samplerate * MAX_DECODE_S)
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
            play_track(read, rewind, lambda block: stream.write(duck(block)), OUT_RATE, stop=stop,
                       cancelled=cancelled, until=until, min_s=min_s)
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


__all__ = ["DECODED_EXT", "Ducker", "SoundEngine", "play_track"]
