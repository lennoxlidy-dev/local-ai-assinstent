"""Optional audio recordings of voice requests (privacy.save_audio; off by default).

Only when the user turns it on is a request's audio written to
``%LOCALAPPDATA%\\Assistant\\recordings`` as a 16 kHz WAV; the conversation retention applies.
"""

from __future__ import annotations

import logging
import time
from pathlib import Path

import numpy as np

from ..audio.sources import RATE, write_wav
from ..core.context import AppContext

log = logging.getLogger(__name__)


def folder(ctx: AppContext) -> Path:
    return ctx.paths.local_dir / "recordings"


def save(ctx: AppContext, audio: np.ndarray, kind: str) -> Path | None:
    if not ctx.config.data.privacy.save_audio or audio.size == 0:
        return None
    d = folder(ctx)
    try:
        d.mkdir(parents=True, exist_ok=True)
        path = d / f"{time.strftime('%Y%m%d-%H%M%S')}-{kind}.wav"
        write_wav(path, audio, RATE)
        return path
    except OSError as e:
        log.warning("could not save the recording: %s", e)
        return None


def purge(ctx: AppContext, retention_days: int) -> int:
    """Delete recordings older than the retention (0 = keep)."""
    d = folder(ctx)
    if retention_days <= 0 or not d.exists():
        return 0
    cutoff = time.time() - retention_days * 86400
    n = 0
    for f in d.glob("*.wav"):
        try:
            if f.stat().st_mtime < cutoff:
                f.unlink()
                n += 1
        except OSError:
            continue
    return n


__all__ = ["folder", "purge", "save"]
