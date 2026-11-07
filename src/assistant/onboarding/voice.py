"""Speech for the setup wizard: speak each question, listen for the answer, record the name / voice-lock takes.

Runs on its own worker thread and reports through ``on_event(kind, data)``:

* ``speaking`` / ``listening`` / ``idle``      - what it is doing (drives the orb)
* ``level`` {level}                            - mic level while listening/recording/monitoring
* ``heard`` {text} / ``nothing``               - the transcript of an answer (or silence)
* ``take`` {n, total} / ``takes_done``         - recording progress (wake word, voice lock)
* ``calibrated`` {result} / ``enrolled``       - results
* ``error`` {message}

Models are used as soon as they are downloaded; ``available()`` says what works right now.
Each new request bumps a generation counter so stale results are dropped.
"""

from __future__ import annotations

import logging
import queue
import threading
import time
from collections.abc import Callable
from typing import Any

import numpy as np

from ..audio.sources import RATE, AudioSource, MicSource
from ..core.cancel import CancelToken
from ..core.context import AppContext

log = logging.getLogger(__name__)

Event = Callable[[str, dict[str, Any]], None]

ENROLL_SENTENCES = {
    "en": ["The quick brown fox jumps over the lazy dog.", "What's the weather like tomorrow?",
           "Please open my documents folder and play some music."],
    "de": ["Franz jagt im komplett verwahrlosten Taxi quer durch Bayern.", "Wie wird das Wetter morgen?",
           "Öffne bitte meinen Dokumente-Ordner und spiel etwas Musik."],
}


class SetupVoice:
    TAKE_S = 2.5          # one "say the name" recording
    ENROLL_S = 4.5        # one voice-lock sentence

    def __init__(self, ctx: AppContext, on_event: Event, *, source_factory: Callable[[], AudioSource] | None = None,
                 player: Any = None, stt: Any = None, speaker: Any = None):
        """``stt`` / ``speaker``: borrow the running voice stack's models (not unloaded on close)."""
        self.ctx = ctx
        self.on_event = on_event
        self.source_factory = source_factory or (lambda: MicSource(ctx.config.data.voice.input_device))
        self._player = player if player is not None else (speaker.player if speaker is not None else None)
        self._speaker: Any = speaker
        self._stt: Any = stt
        self._borrowed = {id(x) for x in (stt, speaker) if x is not None}
        self._wake: Any = None
        self._lock_model: Any = None
        self._q: queue.Queue[tuple[int, Callable[[int], None]] | None] = queue.Queue()
        self._gen = 0
        self._token = CancelToken()
        self._monitor: threading.Thread | None = None
        self._monitor_stop = threading.Event()
        self._thread = threading.Thread(target=self._worker, name="setup-voice", daemon=True)
        self._thread.start()

    # ------------------------------------------------------------------ availability
    def _installed(self, mid: str) -> bool:
        from ..voice.models import installed

        return installed(mid, self.ctx.paths.models_dir)

    def available(self) -> dict[str, bool]:
        return {"speak": self._installed("tts"), "listen": self._installed("stt") and self._installed("vad"),
                "wake": self._installed("kws"), "lock": self._installed("speaker")}

    # ------------------------------------------------------------------ lazy models
    def player(self) -> Any:
        if self._player is None:
            from ..audio.player import Player

            self._player = Player(self.ctx.config.data.voice.output_device)
        return self._player

    def speaker(self) -> Any:
        if self._speaker is None:
            from ..voice.runtime import model_path
            from ..voice.tts import Speaker

            self._speaker = Speaker(model_path(self.ctx, "tts"), self.player(), keep_loaded_min=0)
        return self._speaker

    def stt(self) -> Any:
        if self._stt is None:
            from ..voice.runtime import model_path
            from ..voice.stt import Transcriber

            self._stt = Transcriber(model_path(self.ctx, "stt"), keep_loaded_min=0)
        return self._stt

    def preload(self) -> None:
        """Warm up what is installed (TTS + STT load in the background while the user reads)."""
        av = self.available()
        if av["speak"]:
            self.speaker().preload()
        if av["listen"]:
            self.stt().preload()

    # ------------------------------------------------------------------ requests (any thread)
    def _submit(self, fn: Callable[[int], None]) -> None:
        self.cancel()
        self._q.put((self._gen, fn))

    def cancel(self) -> None:
        """Stop talking/listening now (new question, window closed, typed answer)."""
        self._gen += 1
        self._token.cancel("superseded")
        self._token = CancelToken()
        if self._speaker is not None:
            self._speaker.stop()

    def ask(self, text: str, *, speak: bool, listen: bool, voice: str | None = None) -> None:
        """Say ``text`` (if speaking is possible) and then listen for one answer (if wanted and possible)."""
        def job(gen: int) -> None:
            if speak and text and self.available()["speak"]:
                self._say(text, gen, voice)
            if listen and gen == self._gen and self.available()["listen"]:
                time.sleep(0.15)                          # let the room go quiet after our own voice
                if gen == self._gen:
                    self._listen(gen)
            if gen == self._gen:
                self.on_event("idle", {})
        self._submit(job)

    def say(self, text: str, voice: str | None = None, lang: str | None = None) -> None:
        def job(gen: int) -> None:
            if self.available()["speak"]:
                self._say(text, gen, voice, lang)
            if gen == self._gen:
                self.on_event("idle", {})
        self._submit(job)

    def record_name(self, name: str, takes: int = 3, intro: str = "") -> None:
        """(Say ``intro``,) then beep + record ~2.5 s, three times; then calibrate the wake word for ``name``."""
        def job(gen: int) -> None:
            if intro and self.available()["speak"]:
                self._say(intro, gen)
            clips = self._record_takes(gen, takes, self.TAKE_S, prompts=None)
            if clips is None or gen != self._gen:
                return
            self.on_event("takes_done", {})
            try:
                result = self._calibrate(name, clips)
            except Exception as e:
                log.exception("wake word calibration failed")
                self.on_event("error", {"message": str(e)})
                return
            if gen == self._gen:
                self.on_event("calibrated", {"result": result})
                self.on_event("idle", {})
        self._submit(job)

    def enroll(self, lang: str) -> None:
        """Voice lock: record three sentences, store the voiceprint."""
        def job(gen: int) -> None:
            sentences = ENROLL_SENTENCES["de" if lang == "de" else "en"]
            clips = self._record_takes(gen, len(sentences), self.ENROLL_S, prompts=sentences)
            if clips is None or gen != self._gen:
                return
            self.on_event("takes_done", {})
            try:
                self._voicelock().enroll(clips)
            except Exception as e:
                log.exception("voice lock enrollment failed")
                self.on_event("error", {"message": str(e)})
                return
            self.on_event("enrolled", {})
            self.on_event("idle", {})
        self._submit(job)

    # ------------------------------------------------------------------ microphone level test
    def start_monitor(self, device: str | None = None) -> None:
        self.stop_monitor()
        self._monitor_stop = threading.Event()
        stop = self._monitor_stop

        def run() -> None:
            try:
                src = MicSource(device) if device is not None else self.source_factory()
                src.start()
            except Exception as e:
                self.on_event("error", {"message": f"microphone: {e}"})
                return
            peak, last = 0.0, 0.0
            try:
                while not stop.is_set():
                    try:
                        f = src.frames.get(timeout=0.3)
                    except queue.Empty:
                        continue
                    if f is None:
                        break
                    rms = float(np.sqrt(np.mean(f * f))) if f.size else 0.0
                    peak = max(peak * 0.97, float(np.max(np.abs(f))) if f.size else 0.0)
                    now = time.monotonic()
                    if now - last > 0.06:
                        last = now
                        self.on_event("level", {"level": rms, "peak": peak})
            finally:
                src.stop()
        self._monitor = threading.Thread(target=run, name="setup-mic-monitor", daemon=True)
        self._monitor.start()

    def stop_monitor(self) -> None:
        self._monitor_stop.set()
        if self._monitor is not None:
            self._monitor.join(1.5)
            self._monitor = None

    def close(self) -> None:
        self.stop_monitor()
        self.cancel()
        self._q.put(None)
        stt, sp = self._stt, self._speaker
        self._stt = self._speaker = self._wake = self._lock_model = None
        if stt is not None and id(stt) not in self._borrowed:
            stt.unload()                 # ~870 MB; the tray app loads its own when it is needed
        if sp is not None and id(sp) not in self._borrowed:
            sp.unload()

    # ------------------------------------------------------------------ worker
    def _worker(self) -> None:
        while True:
            item = self._q.get()
            if item is None:
                return
            gen, fn = item
            if gen != self._gen:
                continue
            try:
                fn(gen)
            except Exception as e:
                log.exception("setup voice task failed")
                self.on_event("error", {"message": str(e)})

    def _say(self, text: str, gen: int, voice: str | None = None, lang: str | None = None) -> None:
        c = self.ctx.config.data
        lang = lang or c.general.language
        sp = self.speaker()
        self.on_event("speaking", {})
        tok = self._token
        sp.say(text, lang, voice or (c.voice.tts_voice_de if lang == "de" else c.voice.tts_voice_en),
               c.voice.tts_speed, tok)
        while gen == self._gen and not sp.wait(tok, timeout=0.1):
            if tok.cancelled:
                break

    def _listen(self, gen: int) -> None:
        from ..voice.endpoint import Endpointer, EpEvent
        from ..voice.runtime import model_path

        ep = Endpointer(model_path(self.ctx, "vad"))
        ep.configure(max(0.8, self.ctx.config.data.voice.silence_timeout_s), 8.0, 15.0)
        ep.reset()
        try:
            src = self.source_factory()
            src.start()
        except Exception as e:
            self.on_event("error", {"message": f"microphone: {e}"})
            return
        self.stt().preload()
        self.on_event("listening", {})
        audio: list[np.ndarray] = []
        result = None
        last = 0.0
        try:
            while gen == self._gen:
                try:
                    f = src.frames.get(timeout=0.3)
                except queue.Empty:
                    continue
                if f is None:
                    break
                audio.append(f)
                now = time.monotonic()
                if now - last > 0.06:
                    last = now
                    self.on_event("level", {"level": float(np.sqrt(np.mean(f * f))) if f.size else 0.0})
                ev = ep.feed(f)
                if ev in (EpEvent.END, EpEvent.MAX):
                    result = "speech"
                    break
                if ev is EpEvent.NO_SPEECH:
                    result = "none"
                    break
        finally:
            src.stop()
        if gen != self._gen:
            return
        if result != "speech":
            self.on_event("nothing", {})
            return
        self.on_event("thinking", {})
        text = self.stt().transcribe(np.concatenate(audio))
        if gen == self._gen:
            self.on_event("heard" if text.strip() else "nothing", {"text": text.strip()})

    def _record_takes(self, gen: int, n: int, seconds: float, prompts: list[str] | None) -> list[np.ndarray] | None:
        from ..audio.earcons import sound
        from ..voice.calibration import record

        clips: list[np.ndarray] = []
        for i in range(n):
            if gen != self._gen:
                return None
            self.on_event("take", {"n": i + 1, "total": n, "prompt": prompts[i] if prompts else ""})
            try:
                self.player().play(sound("wake"))
                time.sleep(0.35)
            except Exception as e:
                log.debug("beep failed: %s", e)
            try:
                clip = record(seconds, source=self.source_factory(),
                              on_level=lambda lv: self.on_event("level", {"level": lv}))
            except Exception as e:
                self.on_event("error", {"message": f"microphone: {e}"})
                return None
            clips.append(clip)
            time.sleep(0.4)
        return clips

    def _calibrate(self, name: str, clips: list[np.ndarray]) -> dict[str, Any]:
        from ..voice.calibration import speech_level_ok
        from ..voice.runtime import model_path
        from ..voice.wakeword import WakeWord

        v = self.ctx.config.data.voice
        if self._wake is None or self._wake.name != name:
            self._wake = WakeWord(model_path(self.ctx, "kws"), name, sensitivity=v.wake_sensitivity,
                                  keywords_file=self.ctx.paths.cache_dir / "wake_keywords_setup.txt")
        heard: list[str] = []
        if self.available()["listen"]:
            for c in clips:
                try:
                    heard.append(self.stt().transcribe(c))
                except Exception as e:
                    log.debug("calibration transcript failed: %s", e)
        res = self._wake.calibrate(clips, heard)
        quiet = sum(not speech_level_ok(c) for c in clips)
        return {"sensitivity": res.sensitivity, "variants": res.variants, "detected": res.detected,
                "total": res.total, "heard": res.heard, "ok": res.ok, "quiet": quiet}

    def _voicelock(self) -> Any:
        if self._lock_model is None:
            from ..voice.runtime import model_path
            from ..voice.voicelock import VoiceLock

            self._lock_model = VoiceLock(model_path(self.ctx, "speaker"), self.ctx.paths.config_dir / "voiceprint.npy",
                                         threshold=self.ctx.config.data.voice.voice_lock_threshold)
        return self._lock_model


def level_verdict(peak: float, rms: float, lang: str = "en") -> tuple[str, str]:
    """(kind, text) for the microphone test: ok | quiet | loud | silent."""
    de = lang == "de"
    if peak < 0.01:
        return "silent", ("Ich höre nichts - ist das richtige Mikrofon gewählt und nicht stummgeschaltet?" if de
                          else "I can't hear anything - is the right microphone selected and not muted?")
    if peak < 0.06:
        return "quiet", ("Etwas leise - sprich lauter oder erhöhe die Mikrofon-Lautstärke in Windows." if de
                         else "A bit quiet - speak up or raise the microphone volume in Windows.")
    if peak > 0.98:
        return "loud", ("Sehr laut - das kann verzerren. Senke die Mikrofon-Lautstärke etwas." if de
                        else "Very loud - this may distort. Lower the microphone volume a little.")
    return "ok", "Gut - ich höre dich klar." if de else "Good - I can hear you clearly."


__all__ = ["ENROLL_SENTENCES", "RATE", "SetupVoice", "level_verdict"]
