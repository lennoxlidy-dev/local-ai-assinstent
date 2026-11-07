"""Audio sources: microphone (sounddevice) or WAV/array (test harness). Always 16 kHz mono float32.

Frames go into a queue; the voice pipeline's own thread consumes them, so the
PortAudio callback never does real work (no glitches, no blocking the driver).
Nothing is written to disk.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
import wave
from pathlib import Path
from typing import Protocol

import numpy as np

log = logging.getLogger(__name__)

RATE = 16000
BLOCK = 640          # 40 ms: the keyword spotter decodes in ~320 ms chunks anyway, so larger blocks cost no
                     # wake latency but halve the wake-ups of the always-on audio thread (measured -12% idle CPU)


class AudioSource(Protocol):
    frames: queue.Queue[np.ndarray | None]  # None = end of stream
    def start(self) -> None: ...
    def stop(self) -> None: ...


def list_devices(kind: str = "input") -> list[str]:
    try:
        import sounddevice as sd

        return [d["name"] for d in sd.query_devices() if d[f"max_{kind}_channels"] > 0]
    except Exception as e:  # no PortAudio / no devices
        log.debug("audio devices unavailable: %s", e)
        return []


def resolve_device(name: str, kind: str = "input") -> int | None:
    """Device index for a (partial) name; None = system default."""
    if not name:
        return None
    import sounddevice as sd

    for i, d in enumerate(sd.query_devices()):
        if d[f"max_{kind}_channels"] > 0 and name.casefold() in d["name"].casefold():
            return i
    log.warning("audio device %r not found; using the default", name)
    return None


class MicSource:
    def __init__(self, device: str = "", max_queue_s: float = 10.0):
        self.device = device
        self.frames: queue.Queue[np.ndarray | None] = queue.Queue(maxsize=int(max_queue_s * RATE / BLOCK))
        self._stream = None
        self._resampler = None
        self._last: np.ndarray | None = None   # last block, for the level meter (computed only when read)
        self.native_rate = RATE

    def start(self) -> None:
        import sounddevice as sd

        dev = resolve_device(self.device, "input")
        try:
            self._stream = sd.InputStream(samplerate=RATE, channels=1, dtype="float32", blocksize=BLOCK,
                                          device=dev, callback=self._callback)
            self.native_rate = RATE
        except Exception as e:  # device can't do 16 kHz (some WASAPI setups): open natively + resample
            import soxr

            info = sd.query_devices(dev, "input")
            self.native_rate = int(info["default_samplerate"])
            log.info("mic can't open at 16 kHz (%s); using %d Hz + resampling", e, self.native_rate)
            self._resampler = soxr.ResampleStream(self.native_rate, RATE, 1, dtype="float32")
            self._stream = sd.InputStream(samplerate=self.native_rate, channels=1, dtype="float32",
                                          blocksize=int(self.native_rate * BLOCK / RATE), device=dev,
                                          callback=self._callback)
        self._stream.start()
        log.info("microphone open (%s)", "default device" if dev is None else self.device)

    def _callback(self, indata, frames, time_info, status) -> None:  # type: ignore[no-untyped-def]
        x = indata[:, 0].copy()
        if self._resampler is not None:
            x = self._resampler.resample_chunk(x)
        if x.size:
            self._last = x
            try:
                self.frames.put_nowait(x)
            except queue.Full:  # consumer stalled: drop oldest audio rather than block the driver
                try:
                    self.frames.get_nowait()
                    self.frames.put_nowait(x)
                except queue.Empty:
                    pass

    @property
    def level(self) -> float:
        """RMS of the latest block (UI orb / level meter)."""
        x = self._last
        return float(np.sqrt(np.mean(x * x))) if x is not None and x.size else 0.0

    @property
    def alive(self) -> bool:
        """False once the device stopped delivering (unplugged headset, driver reset)."""
        return self._stream is not None and bool(self._stream.active)

    def stop(self) -> None:
        if self._stream is not None:
            try:
                self._stream.stop()
                self._stream.close()
            except Exception as e:   # already dead (device gone)
                log.debug("closing microphone: %s", e)
            finally:
                self._stream = None
                log.info("microphone closed")
        self.frames.put(None)


def read_wav(path: Path) -> np.ndarray:
    """PCM16/32 or float WAV -> 16 kHz mono float32."""
    with wave.open(str(path), "rb") as w:
        sr, ch, width, n = w.getframerate(), w.getnchannels(), w.getsampwidth(), w.getnframes()
        raw = w.readframes(n)
    if width == 2:
        x = np.frombuffer(raw, dtype="<i2").astype(np.float32) / 32768.0
    elif width == 4:
        x = np.frombuffer(raw, dtype="<i4").astype(np.float32) / 2147483648.0
    elif width == 1:
        x = (np.frombuffer(raw, dtype=np.uint8).astype(np.float32) - 128.0) / 128.0
    else:
        raise ValueError(f"unsupported WAV sample width {width}")
    if ch > 1:
        x = x.reshape(-1, ch).mean(axis=1)
    if sr != RATE:
        import soxr

        x = soxr.resample(x, sr, RATE).astype(np.float32)
    return x


def write_wav(path: Path, samples: np.ndarray, sr: int) -> None:
    pcm = (np.clip(samples, -1.0, 1.0) * 32767).astype("<i2")
    with wave.open(str(path), "wb") as w:
        w.setnchannels(1)
        w.setsampwidth(2)
        w.setframerate(sr)
        w.writeframes(pcm.tobytes())


class ArraySource:
    """Feeds prepared audio (e.g. WAV files) as if it came from a microphone."""

    def __init__(self, *clips: np.ndarray, realtime: bool = False, gap_s: float = 0.0, tail_s: float = 1.5):
        self.frames: queue.Queue[np.ndarray | None] = queue.Queue()
        silence = np.zeros(int(gap_s * RATE), np.float32)
        parts: list[np.ndarray] = []
        for c in clips:
            parts += [c.astype(np.float32), silence]
        parts.append(np.zeros(int(tail_s * RATE), np.float32))
        self.audio = np.concatenate(parts) if parts else np.zeros(0, np.float32)
        self.realtime = realtime
        self.level = 0.0
        self._thread: threading.Thread | None = None
        self._stop = threading.Event()

    @classmethod
    def from_wavs(cls, *paths: Path, **kw: object) -> ArraySource:
        return cls(*(read_wav(Path(p)) for p in paths), **kw)  # type: ignore[arg-type]

    def start(self) -> None:
        def run() -> None:
            t0 = time.perf_counter()
            for i in range(0, len(self.audio), BLOCK):
                if self._stop.is_set():
                    break
                x = self.audio[i:i + BLOCK]
                self.level = float(np.sqrt(np.mean(x * x))) if x.size else 0.0
                self.frames.put(x)
                if self.realtime:
                    delay = t0 + (i + BLOCK) / RATE - time.perf_counter()
                    if delay > 0:
                        time.sleep(delay)
            self.frames.put(None)

        self._thread = threading.Thread(target=run, name="audio-feed", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()


class RingBuffer:
    """The last few seconds of audio (pre-roll: the wake word itself, for the STT double-check)."""

    def __init__(self, seconds: float):
        self.buf = np.zeros(int(seconds * RATE), np.float32)
        self.pos = 0
        self.filled = 0

    def write(self, x: np.ndarray) -> None:
        n = len(x)
        if n >= len(self.buf):
            self.buf[:] = x[-len(self.buf):]
            self.pos, self.filled = 0, len(self.buf)
            return
        end = self.pos + n
        if end <= len(self.buf):
            self.buf[self.pos:end] = x
        else:
            k = len(self.buf) - self.pos
            self.buf[self.pos:] = x[:k]
            self.buf[:n - k] = x[k:]
        self.pos = end % len(self.buf)
        self.filled = min(len(self.buf), self.filled + n)

    def last(self, seconds: float) -> np.ndarray:
        n = min(int(seconds * RATE), self.filled)
        if n <= 0:
            return np.zeros(0, np.float32)
        start = (self.pos - n) % len(self.buf)
        if start + n <= len(self.buf):
            return self.buf[start:start + n].copy()
        return np.concatenate([self.buf[start:], self.buf[:(start + n) % len(self.buf)]])
