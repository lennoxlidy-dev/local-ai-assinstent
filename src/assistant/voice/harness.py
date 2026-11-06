"""Test harness: run WAV files through the real voice pipeline instead of the microphone.

    python -m assistant --voice-test a.wav b.wav [--speak-out reply.wav]
    python -m assistant --say "Jarvis, wie spät ist es?" --lang de --out test.wav   # make test audio

Prints every pipeline event with a timestamp and the per-stage timings, so
latency can be checked without a mic (the same events drive the popup).
"""

from __future__ import annotations

import sys
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np

from ..audio.player import NullPlayer
from ..audio.sources import RATE, ArraySource, read_wav, write_wav
from ..core.context import AppContext
from ..core.events import Event

SHOWN = ("voice.", "assistant.reply", "agent.tool", "llm.state", "ui.notify")


def synthesize(ctx: AppContext, text: str, lang: str, out: Path, voice: str = "F1") -> Path:
    from .runtime import model_path
    from .tts import Speaker

    sp = Speaker(model_path(ctx, "tts"), NullPlayer())
    samples, sr = sp.synthesize(text, lang, voice)
    pad = np.zeros(int(0.4 * sr), np.float32)
    write_wav(out, np.concatenate([pad, samples, pad]), sr)
    return out


def run_wavs(ctx: AppContext, assistant: Any, wavs: list[Path], *, realtime: bool = True, gap_s: float = 2.0,
             speak_out: Path | None = None, out: Any = None) -> list[Event]:
    from .runtime import build_voice

    out = out or sys.stdout
    events: list[Event] = []
    t0 = time.perf_counter()

    def show(e: Event) -> None:
        if e.topic.startswith(SHOWN) and e.topic != "voice.level":
            events.append(e)
            data = {k: v for k, v in e.data.items() if k not in ("gen",)}
            print(f"{time.perf_counter() - t0:7.2f}s  {e.topic:<20} {data}", file=out, flush=True)

    unsub = ctx.bus.subscribe("*", show)
    player = NullPlayer(realtime=realtime)
    clips = [read_wav(p) for p in wavs]
    stack = build_voice(ctx, assistant, source_factory=lambda: ArraySource(*clips, realtime=realtime, gap_s=gap_s,
                                                                           tail_s=8.0), player=player)
    done = threading.Event()
    stack.pipeline.start()
    while stack.pipeline._running:  # until the WAV source ran out and the last request finished
        time.sleep(0.1)
    deadline = time.time() + 120
    while time.time() < deadline and stack.pipeline.state.value not in ("idle", "off", "follow_up"):
        time.sleep(0.1)
    done.set()
    stack.pipeline.stop()
    unsub()
    if speak_out is not None and player.played:
        write_wav(speak_out, np.concatenate(player.played), 44100)
        print(f"spoken output saved to {speak_out}", file=out)
    print(f"total {time.perf_counter() - t0:.1f}s for {sum(len(c) for c in clips) / RATE:.1f}s of audio", file=out)
    return events
