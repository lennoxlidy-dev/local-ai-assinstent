"""Short UI sounds synthesized at runtime (no asset files, no licensing questions)."""

from __future__ import annotations

from functools import cache

import numpy as np

from .player import OUT_RATE


def _tone(freqs: list[tuple[float, float]], volume: float = 0.18) -> np.ndarray:
    """Sequence of (frequency Hz, duration s) with soft attack/release and a bell-like overtone."""
    parts = []
    for f, d in freqs:
        n = int(d * OUT_RATE)
        t = np.arange(n) / OUT_RATE
        env = np.minimum(1.0, t / 0.006) * np.exp(-t * 9.0)
        wave = np.sin(2 * np.pi * f * t) + 0.25 * np.sin(2 * np.pi * 2 * f * t)
        parts.append((wave * env * volume).astype(np.float32))
    return np.concatenate(parts)


@cache
def sound(name: str) -> np.ndarray:
    if name == "wake":        # rising two-note chime: "I'm listening"
        return _tone([(784.0, 0.07), (1175.0, 0.16)])
    if name == "end":         # soft single note: "got it, working"
        return _tone([(988.0, 0.09)], volume=0.10)
    if name == "error":       # falling: "didn't work / didn't hear you"
        return _tone([(523.0, 0.08), (392.0, 0.18)], volume=0.14)
    if name == "alarm":       # timer/reminder
        return np.concatenate([_tone([(1047.0, 0.12), (1319.0, 0.12)], volume=0.22)] * 3)
    raise KeyError(name)
