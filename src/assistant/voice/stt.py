"""Speech-to-text: NVIDIA Parakeet TDT 0.6B v3 (int8, CPU) via sherpa-onnx.

~870 MB resident when loaded, so it is loaded on demand: the pipeline calls
``preload()`` the moment the wake word fires (loading overlaps with you
speaking) and it is unloaded after ``keep_loaded_min`` idle minutes.
"""

from __future__ import annotations

import gc
import logging
import os
import threading
import time
from pathlib import Path

import numpy as np

from ..core.timing import timed

log = logging.getLogger(__name__)


class Transcriber:
    def __init__(self, model_dir: Path, *, threads: int | None = None, keep_loaded_min: float = 10.0):
        self.dir = Path(model_dir)
        self.threads = threads or max(2, min(8, (os.cpu_count() or 4) // 2))
        self.keep_loaded_s = keep_loaded_min * 60
        self._rec = None
        self._lock = threading.Lock()
        self._loading: threading.Thread | None = None
        self._last_use = 0.0

    @property
    def loaded(self) -> bool:
        return self._rec is not None

    def _load(self) -> None:
        with self._lock:
            if self._rec is not None:
                return
            import sherpa_onnx as so

            with timed("stt_load"):
                d = self.dir
                self._rec = so.OfflineRecognizer.from_transducer(
                    encoder=str(d / "encoder.int8.onnx"), decoder=str(d / "decoder.int8.onnx"),
                    joiner=str(d / "joiner.int8.onnx"), tokens=str(d / "tokens.txt"), num_threads=self.threads,
                    model_type="nemo_transducer")
            self._last_use = time.monotonic()
            log.info("speech recognition loaded (%d threads)", self.threads)

    def preload(self) -> None:
        """Start loading in the background (no-op if loaded/loading)."""
        if self._rec is not None or (self._loading is not None and self._loading.is_alive()):
            return
        self._loading = threading.Thread(target=self._load, name="stt-load", daemon=True)
        self._loading.start()

    def transcribe(self, samples: np.ndarray) -> str:
        if self._rec is None:
            self._load()
        with self._lock:
            rec = self._rec
            assert rec is not None
            s = rec.create_stream()
            s.accept_waveform(16000, samples)
            rec.decode_stream(s)
            self._last_use = time.monotonic()
            return s.result.text.strip()

    def unload_if_idle(self) -> bool:
        if self._rec is None or self.keep_loaded_s <= 0:
            return False
        if time.monotonic() - self._last_use < self.keep_loaded_s:
            return False
        with self._lock:
            self._rec = None
        gc.collect()
        log.info("speech recognition unloaded after %.0f idle minutes", self.keep_loaded_s / 60)
        return True
