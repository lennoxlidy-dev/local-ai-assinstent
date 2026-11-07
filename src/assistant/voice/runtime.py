"""Build the voice stack from the config (and keep it in sync with config changes)."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from ..audio.player import NullPlayer, Player
from ..audio.sources import AudioSource, MicSource
from ..core.context import AppContext
from ..core.events import Event, Topic
from .endpoint import Endpointer
from .models import VOICE_MODELS, installed, missing, voice_dir
from .pipeline import VoicePipeline
from .stt import Transcriber
from .tts import Speaker
from .voicelock import VoiceLock
from .wakeword import WakeWord

log = logging.getLogger(__name__)


class VoiceUnavailable(RuntimeError):
    pass


@dataclass
class VoiceStack:
    pipeline: VoicePipeline
    wake: WakeWord
    stt: Transcriber
    speaker: Speaker
    voicelock: VoiceLock


def model_path(ctx: AppContext, model_id: str) -> Path:
    m = VOICE_MODELS[model_id]
    return voice_dir(ctx.paths.models_dir) / (m.folder or m.check)


def build_voice(ctx: AppContext, assistant: Any, *, source_factory: Callable[[], AudioSource] | None = None,
                player: Any = None, headless: bool = False) -> VoiceStack:
    gone = missing(ctx.paths.models_dir)
    if gone:
        raise VoiceUnavailable("voice models not downloaded: " + ", ".join(gone) + " (run setup)")
    v = ctx.config.data.voice
    wake = WakeWord(model_path(ctx, "kws"), ctx.config.data.general.assistant_name, sensitivity=v.wake_sensitivity,
                    variants=list(v.wake_variants), keywords_file=ctx.paths.cache_dir / "wake_keywords.txt")
    ep = Endpointer(model_path(ctx, "vad"))
    stt = Transcriber(model_path(ctx, "stt"), keep_loaded_min=v.stt_keep_loaded_min)
    if player is None:
        player = NullPlayer() if headless else Player(v.output_device)
    speaker = Speaker(model_path(ctx, "tts"), player, keep_loaded_min=v.stt_keep_loaded_min)
    speaker.volume = v.tts_volume / 100
    lock = VoiceLock(model_path(ctx, "speaker"), ctx.paths.config_dir / "voiceprint.npy",
                     threshold=v.voice_lock_threshold)
    if v.voice_lock and not installed("speaker", ctx.paths.models_dir):
        log.warning("voice lock is on but its model isn't downloaded; voice lock inactive")
    factory = source_factory or (lambda: MicSource(ctx.config.data.voice.input_device))
    pipeline = VoicePipeline(ctx, assistant, source_factory=factory, wake=wake, endpointer=ep, stt=stt,
                             speaker=speaker, voicelock=lock if installed("speaker", ctx.paths.models_dir) else None)

    def on_config(e: Event) -> None:
        keys = set(e.data.get("keys", []))
        if keys & {"general.assistant_name", "voice.wake_sensitivity", "voice.wake_variants"}:
            try:
                wake.configure(ctx.config.data.general.assistant_name, ctx.config.data.voice.wake_sensitivity,
                               list(ctx.config.data.voice.wake_variants))
            except ValueError as err:
                ctx.bus.publish(Topic.NOTIFY, title="Wake word", message=str(err), level="warning")
        v = ctx.config.data.voice
        if "voice.voice_lock_threshold" in keys:
            lock.threshold = v.voice_lock_threshold
        if "voice.tts_volume" in keys:
            speaker.volume = v.tts_volume / 100
        if "voice.stt_keep_loaded_min" in keys:
            stt.keep_loaded_s = speaker.keep_loaded_s = v.stt_keep_loaded_min * 60
        if "voice.output_device" in keys and hasattr(player, "device"):
            player.device = v.output_device      # used when the output stream is next opened
        if "voice.input_device" in keys and pipeline.running:
            def reopen() -> None:                # switch microphones without restarting the app
                pipeline.stop()
                try:
                    pipeline.start()
                except Exception as err:
                    ctx.bus.publish(Topic.NOTIFY, title="Microphone", message=str(err), level="warning")

            threading.Thread(target=reopen, name="mic-switch", daemon=True).start()

    ctx.bus.subscribe(Topic.CONFIG_CHANGED, on_config)
    return VoiceStack(pipeline, wake, stt, speaker, lock)
