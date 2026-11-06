"""Recording helpers for wake-word calibration and voice-lock enrollment.

Used by the debug CLI now and by the onboarding wizard (say the name 3x) later.
Recordings stay in memory.
"""

from __future__ import annotations

import logging
import queue
import time
from collections.abc import Callable
from typing import Any

import numpy as np

from ..audio.sources import RATE, MicSource
from ..core.context import AppContext
from .wakeword import CalibrationResult

log = logging.getLogger(__name__)


def record(seconds: float, device: str = "", on_level: Callable[[float], None] | None = None,
           source: Any = None) -> np.ndarray:
    """Record ``seconds`` of 16 kHz mono audio from the microphone (or a given source)."""
    src = source or MicSource(device)
    src.start()
    frames: list[np.ndarray] = []
    need = int(seconds * RATE)
    got = 0
    deadline = time.monotonic() + seconds + 3.0
    try:
        while got < need and time.monotonic() < deadline:
            try:
                f = src.frames.get(timeout=0.5)
            except queue.Empty:
                continue
            if f is None:
                break
            frames.append(f)
            got += len(f)
            if on_level is not None:
                on_level(float(np.sqrt(np.mean(f * f))) if len(f) else 0.0)
    finally:
        src.stop()
    return np.concatenate(frames)[:need] if frames else np.zeros(0, np.float32)


def calibrate_wake_word(ctx: AppContext, wake: Any, stt: Any, recordings: list[np.ndarray],
                        save: bool = True) -> CalibrationResult:
    """Tune sensitivity + spelling variants so *your* way of saying the name is detected."""
    heard = []
    for r in recordings:
        try:
            heard.append(stt.transcribe(r))
        except Exception as e:  # STT missing/failed: calibrate on the audio alone
            log.debug("calibration transcript failed: %s", e)
    res = wake.calibrate(recordings, heard)
    log.info("wake word calibration: %d/%d detected at sensitivity %.1f, variants %s",
             res.detected, res.total, res.sensitivity, res.variants)
    if save:
        ctx.config.update({"voice.wake_sensitivity": res.sensitivity, "voice.wake_variants": res.variants})
    return res


def speech_level_ok(audio: np.ndarray) -> bool:
    """Rough check that a recording contains speech at a usable level."""
    if audio.size < RATE // 2:
        return False
    rms = float(np.sqrt(np.mean(audio * audio)))
    peak = float(np.max(np.abs(audio)))
    return rms > 0.004 and peak > 0.03


__all__ = ["calibrate_wake_word", "record", "speech_level_ok"]
