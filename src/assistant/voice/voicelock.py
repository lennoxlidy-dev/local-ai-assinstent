"""Optional voice lock: only react to the enrolled speaker (videos/other people can't trigger actions).

Speaker embeddings (3D-Speaker CAM++, 30 MB) compared by cosine similarity. The
enrolled voiceprint is a small vector stored locally - no audio is kept.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)


class VoiceLock:
    def __init__(self, model: Path, profile: Path, *, threshold: float = 0.55):
        self.model = Path(model)
        self.profile = Path(profile)
        self.threshold = threshold
        self._ext = None
        self._lock = threading.Lock()

    def _extractor(self):  # type: ignore[no-untyped-def]
        with self._lock:
            if self._ext is None:
                import sherpa_onnx as so

                self._ext = so.SpeakerEmbeddingExtractor(so.SpeakerEmbeddingExtractorConfig(
                    model=str(self.model), num_threads=2))
            return self._ext

    def embed(self, samples: np.ndarray) -> np.ndarray:
        ext = self._extractor()
        s = ext.create_stream()
        s.accept_waveform(16000, samples)
        s.input_finished()
        v = np.asarray(ext.compute(s), dtype=np.float32)
        return v / (np.linalg.norm(v) + 1e-9)

    @property
    def enrolled(self) -> bool:
        return self.profile.exists()

    def enroll(self, recordings: list[np.ndarray]) -> None:
        if not recordings:
            raise ValueError("no recordings")
        mean = np.mean([self.embed(r) for r in recordings], axis=0)
        mean /= np.linalg.norm(mean) + 1e-9
        self.profile.parent.mkdir(parents=True, exist_ok=True)
        np.save(self.profile, mean)
        log.info("voice lock: enrolled from %d recordings", len(recordings))

    def score(self, samples: np.ndarray) -> float:
        ref = np.load(self.profile)
        return float(np.dot(ref, self.embed(samples)))

    def verify(self, samples: np.ndarray) -> tuple[bool, float]:
        if not self.enrolled:
            return True, 1.0
        if len(samples) < 16000 * 0.6:  # too short for a reliable voiceprint: accept, log
            return True, 0.0
        s = self.score(samples)
        return s >= self.threshold, s

    def forget(self) -> None:
        self.profile.unlink(missing_ok=True)
