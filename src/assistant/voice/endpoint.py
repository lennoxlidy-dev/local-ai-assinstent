"""End-of-utterance detection with Silero VAD (sherpa-onnx), with the timeouts from the settings.

* ``silence_timeout``: how long you may pause before the request is considered complete (default 1 s)
* ``wait_for_speech``: after the wake word/hotkey, how long to wait for the first word
* ``max_utterance``: hard cap
"""

from __future__ import annotations

import logging
from enum import StrEnum
from pathlib import Path

import numpy as np

log = logging.getLogger(__name__)

FRAME_S = 0.02


class EpEvent(StrEnum):
    SPEECH = "speech"        # speech started
    END = "end"              # speech ended (silence timeout)
    NO_SPEECH = "no_speech"  # nobody spoke within wait_for_speech
    MAX = "max"              # max length reached


class Endpointer:
    def __init__(self, vad_model: Path, *, threshold: float = 0.5):
        import sherpa_onnx as so

        cfg = so.VadModelConfig(silero_vad=so.SileroVadModelConfig(
            model=str(vad_model), threshold=threshold, min_silence_duration=0.15, min_speech_duration=0.1,
            window_size=512, max_speech_duration=120.0), sample_rate=16000, num_threads=1)
        self.vad = so.VoiceActivityDetector(cfg, buffer_size_in_seconds=4)
        self.silence_timeout = 1.0
        self.wait_for_speech = 5.0
        self.max_utterance = 20.0
        self.reset()

    def configure(self, silence_timeout: float, wait_for_speech: float, max_utterance: float) -> None:
        self.silence_timeout, self.wait_for_speech, self.max_utterance = silence_timeout, wait_for_speech, max_utterance

    def reset(self, in_speech: bool = False) -> None:
        self.vad.reset()
        self.t = 0.0
        self.spoke = in_speech
        self.speech_s = 0.0
        self.last_speech = 0.0

    @property
    def is_speech(self) -> bool:
        return bool(self.vad.is_speech_detected())

    def feed(self, frame: np.ndarray) -> EpEvent | None:
        self.vad.accept_waveform(frame)
        while not self.vad.empty():   # we don't need its segment copies; keep memory flat
            self.vad.pop()
        self.t += len(frame) / 16000
        event = None
        if self.vad.is_speech_detected():
            if not self.spoke:
                event = EpEvent.SPEECH
            self.spoke = True
            self.speech_s += len(frame) / 16000
            self.last_speech = self.t
        if self.t >= self.max_utterance:
            return EpEvent.MAX
        if not self.spoke:
            return EpEvent.NO_SPEECH if self.t >= self.wait_for_speech else event
        if self.t - self.last_speech >= self.silence_timeout:
            return EpEvent.END
        return event
