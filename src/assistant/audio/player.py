"""Audio output: a streaming player for TTS sentences and earcons, stoppable instantly (panic)."""

from __future__ import annotations

import collections
import logging
import threading
import time

import numpy as np

log = logging.getLogger(__name__)

OUT_RATE = 44100   # Supertonic's native rate; earcons are generated at it too


class Player:
    """Queue float32 chunks; a PortAudio callback plays them back-to-back. ``stop()`` is immediate."""

    IDLE_CLOSE_S = 3.0

    def __init__(self, device: str = ""):
        self.device = device
        self._chunks: collections.deque[np.ndarray] = collections.deque()
        self._cur = np.zeros(0, np.float32)
        self._lock = threading.Lock()
        self._stream = None
        self._idle_since = 0.0
        self._done = threading.Event()
        self._done.set()
        self.level = 0.0

    def play(self, samples: np.ndarray, sr: int = OUT_RATE) -> None:
        x = np.asarray(samples, dtype=np.float32)
        if sr != OUT_RATE:
            import soxr

            x = soxr.resample(x, sr, OUT_RATE).astype(np.float32)
        with self._lock:
            self._chunks.append(x)
            self._done.clear()
        try:
            self._ensure_stream()
        except Exception:
            self.stop()          # no output device: drop the audio instead of waiting for it forever
            raise

    def stop(self) -> None:
        with self._lock:
            self._chunks.clear()
            self._cur = np.zeros(0, np.float32)
            self._done.set()

    @property
    def busy(self) -> bool:
        return not self._done.is_set()

    def wait(self, timeout: float | None = None) -> bool:
        return self._done.wait(timeout)

    # ------------------------------------------------------------------ PortAudio
    def _ensure_stream(self) -> None:
        if self._stream is not None:
            return
        import sounddevice as sd

        from .sources import resolve_device

        self._stream = sd.OutputStream(samplerate=OUT_RATE, channels=1, dtype="float32",
                                       device=resolve_device(self.device, "output"), callback=self._callback,
                                       blocksize=1024)
        self._stream.start()
        threading.Thread(target=self._closer, name="audio-out-idle", daemon=True).start()

    def _callback(self, outdata, frames, time_info, status) -> None:  # type: ignore[no-untyped-def]
        out = outdata[:, 0]
        filled = 0
        with self._lock:
            while filled < frames:
                if not self._cur.size:
                    if not self._chunks:
                        break
                    self._cur = self._chunks.popleft()
                n = min(frames - filled, self._cur.size)
                out[filled:filled + n] = self._cur[:n]
                self._cur = self._cur[n:]
                filled += n
            if filled < frames:
                out[filled:] = 0.0
            if not self._cur.size and not self._chunks:
                if not self._done.is_set():
                    self._idle_since = time.monotonic()
                self._done.set()
        self.level = float(np.sqrt(np.mean(out * out))) if frames else 0.0

    def _closer(self) -> None:
        """Release the audio device when idle (lets Windows sleep, frees exclusive-mode apps)."""
        while self._stream is not None:
            time.sleep(0.5)
            if self._done.is_set() and time.monotonic() - self._idle_since > self.IDLE_CLOSE_S:
                with self._lock:
                    if self._chunks or self._cur.size:
                        continue
                    stream, self._stream = self._stream, None
                try:
                    stream.stop()
                    stream.close()
                except Exception:
                    pass
                return


class NullPlayer:
    """Headless player for tests/CLI harness: records what would be played; 'plays' instantly or in real time."""

    def __init__(self, realtime: bool = False):
        self.realtime = realtime
        self.played: list[np.ndarray] = []
        self._stop = threading.Event()
        self.level = 0.0

    def play(self, samples: np.ndarray, sr: int = OUT_RATE) -> None:
        self.played.append(np.asarray(samples, np.float32))
        if self.realtime:
            self._stop.clear()
            self._stop.wait(len(samples) / sr)

    def stop(self) -> None:
        self._stop.set()

    @property
    def busy(self) -> bool:
        return False

    def wait(self, timeout: float | None = None) -> bool:
        return True

    def audio(self) -> np.ndarray:
        return np.concatenate(self.played) if self.played else np.zeros(0, np.float32)
