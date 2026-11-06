"""Text-to-speech: Supertonic 3 (int8, CPU, English + German), streamed sentence by sentence.

``SentenceStream`` turns the LLM's token stream into sentences, so speech starts
after the first sentence instead of after the whole answer.
"""

from __future__ import annotations

import logging
import queue
import re
import threading
import time
from collections.abc import Callable
from pathlib import Path

import numpy as np

from ..core.cancel import CancelToken
from .models import TTS_VOICES

log = logging.getLogger(__name__)

_SENT_END = re.compile(r"(?<=[.!?…])[\"”»)]?\s+|(?<=[:;])\s+(?=\S{3,})|\n+")
_ABBREV = re.compile(r"\b(z\.B|d\.h|u\.a|bzw|ca|Dr|Mr|Mrs|Ms|Nr|etc|vs|e\.g|i\.e|St)\.$", re.I)


def split_sentences(text: str) -> list[str]:
    parts, buf = [], ""
    for piece in re.split(r"(?<=[.!?…])\s+|\n+", text.strip()):
        buf = f"{buf} {piece}".strip() if buf else piece
        if _ABBREV.search(buf) or re.search(r"\d\.$", buf):   # "z.B." / "3." are not sentence ends
            continue
        if buf:
            parts.append(buf)
        buf = ""
    if buf:
        parts.append(buf)
    return [p for p in (s.strip() for s in parts) if p]


def speakable(text: str) -> str:
    """Remove what shouldn't be read aloud (markdown, URLs, code)."""
    t = re.sub(r"```.*?```", " ", text, flags=re.S)
    t = re.sub(r"`([^`]*)`", r"\1", t)
    t = re.sub(r"https?://\S+", "a link", t)
    t = re.sub(r"[*_#>|]+", " ", t)
    t = re.sub(r"\[(.*?)\]\(.*?\)", r"\1", t)
    return re.sub(r"\s+", " ", t).strip()


class SentenceStream:
    """Feed LLM text deltas; ``on_sentence`` is called with each complete sentence."""

    MIN_CHARS = 12

    def __init__(self, on_sentence: Callable[[str], None]):
        self.on_sentence = on_sentence
        self.buf = ""
        self.emitted = ""

    def feed(self, delta: str) -> None:
        self.buf += delta
        while True:
            m = _SENT_END.search(self.buf)
            if m is None:
                return
            head = self.buf[:m.start()].strip()
            if len(head) < self.MIN_CHARS or _ABBREV.search(head) or re.search(r"\d\.$", head):
                # too short to be worth a separate TTS call, or an abbreviation: wait for more
                nxt = _SENT_END.search(self.buf, m.end())
                if nxt is None:
                    return
                m = nxt
                head = self.buf[:m.start()].strip()
            self.buf = self.buf[m.end():]
            self._emit(head)

    def flush(self) -> None:
        if self.buf.strip():
            self._emit(self.buf.strip())
        self.buf = ""

    def _emit(self, sentence: str) -> None:
        if sentence:
            self.emitted += (" " if self.emitted else "") + sentence
            self.on_sentence(sentence)


class Speaker:
    """Owns the TTS model and a worker that synthesizes queued sentences into the player."""

    def __init__(self, model_dir: Path, player, *, threads: int = 4,  # type: ignore[no-untyped-def]
                 keep_loaded_min: float = 10.0):
        self.dir = Path(model_dir)
        self.player = player
        self.threads = threads
        self.keep_loaded_s = keep_loaded_min * 60
        self._last_use = 0.0
        self._tts = None
        self._lock = threading.Lock()
        self._q: queue.Queue[tuple[str, str, int, float, CancelToken] | None] = queue.Queue()
        self._idle = threading.Event()
        self._idle.set()
        self.speaking_text = ""
        self.first_audio_at: float | None = None
        threading.Thread(target=self._worker, name="tts", daemon=True).start()

    def _load(self):  # type: ignore[no-untyped-def]
        with self._lock:
            if self._tts is None:
                import sherpa_onnx as so

                d = self.dir
                self._tts = so.OfflineTts(so.OfflineTtsConfig(model=so.OfflineTtsModelConfig(
                    supertonic=so.OfflineTtsSupertonicModelConfig(
                        duration_predictor=str(d / "duration_predictor.int8.onnx"),
                        text_encoder=str(d / "text_encoder.int8.onnx"),
                        vector_estimator=str(d / "vector_estimator.int8.onnx"), vocoder=str(d / "vocoder.int8.onnx"),
                        tts_json=str(d / "tts.json"), unicode_indexer=str(d / "unicode_indexer.bin"),
                        voice_style=str(d / "voice.bin")), num_threads=self.threads)))
                log.info("speech output loaded (Supertonic 3)")
            return self._tts

    def preload(self) -> None:
        threading.Thread(target=self._load, name="tts-load", daemon=True).start()

    def synthesize(self, text: str, lang: str, voice: str = "F1", speed: float = 1.0) -> tuple[np.ndarray, int]:
        import sherpa_onnx as so

        tts = self._load()
        self._last_use = time.monotonic()
        g = so.GenerationConfig()
        g.sid = TTS_VOICES.get(voice, 0)
        g.speed = speed
        g.extra = {"lang": "de" if lang == "de" else "en"}
        audio = tts.generate(text, g)
        return np.asarray(audio.samples, dtype=np.float32), int(audio.sample_rate)

    # ------------------------------------------------------------------ queue API
    def say(self, text: str, lang: str, voice: str, speed: float, cancel: CancelToken) -> None:
        """Queue text (split into sentences); returns immediately."""
        for s in split_sentences(speakable(text)):
            self._idle.clear()
            self._q.put((s, lang, TTS_VOICES.get(voice, 0), speed, cancel))

    def wait(self, cancel: CancelToken | None = None, timeout: float = 120.0) -> bool:
        """Block until everything queued has been played (or cancelled)."""
        deadline = time.monotonic() + timeout
        while not self._idle.wait(0.05):
            if (cancel is not None and cancel.cancelled) or time.monotonic() > deadline:
                return False
        while self.player.busy:
            if (cancel is not None and cancel.cancelled) or time.monotonic() > deadline:
                return False
            self.player.wait(0.05)
        return True

    def unload_if_idle(self) -> bool:
        """Free the speech model's RAM after a quiet spell (it is preloaded again at the next wake word)."""
        if self._tts is None or self.keep_loaded_s <= 0 or not self._idle.is_set():
            return False
        if time.monotonic() - self._last_use < self.keep_loaded_s:
            return False
        with self._lock:
            self._tts = None
        import gc

        gc.collect()
        log.info("speech output unloaded after %.0f idle minutes", self.keep_loaded_s / 60)
        return True

    def stop(self) -> None:
        """Panic/barge-in: drop queued sentences and silence the player now."""
        try:
            while True:
                self._q.get_nowait()
        except queue.Empty:
            pass
        self.player.stop()
        self._idle.set()
        self.speaking_text = ""

    def _worker(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                return
            text, lang, sid, speed, cancel = item
            if cancel.cancelled:
                if self._q.empty():
                    self._idle.set()
                continue
            try:
                voice = next((k for k, v in TTS_VOICES.items() if v == sid), "F1")
                t0 = time.perf_counter()
                samples, sr = self.synthesize(text, lang, voice, speed)
                log.debug("TIMING stage=tts chars=%d ms=%.1f audio_s=%.2f", len(text),
                          (time.perf_counter() - t0) * 1000, len(samples) / sr)
                if not cancel.cancelled:
                    self.speaking_text = text
                    if self.first_audio_at is None:
                        self.first_audio_at = time.perf_counter()
                    self.player.play(samples, sr)
            except Exception:
                log.exception("text-to-speech failed")
            finally:
                if self._q.empty():
                    self._idle.set()
