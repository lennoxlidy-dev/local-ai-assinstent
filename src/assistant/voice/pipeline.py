"""The voice pipeline: mic -> wake word -> VAD endpointing -> STT -> assistant -> TTS -> follow-up.

Threads:
* ``voice-audio`` consumes 20 ms frames (wake word / VAD only - cheap, EcoQoS on Windows)
* ``voice-request`` per utterance: transcription, assistant, speech output
* the TTS worker (``Speaker``) synthesizes queued sentences while earlier ones play

Barge-in: saying the name while it is talking stops the speech at once, so
"<name>, stop" works mid-sentence. A generation counter makes sure an
interrupted request can't speak or change state afterwards.
"""

from __future__ import annotations

import logging
import queue
import re
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import Any

import numpy as np

from ..audio.earcons import sound
from ..audio.sources import RATE, AudioSource, RingBuffer
from ..core.cancel import CancelToken
from ..core.context import AppContext
from ..core.state import Phase
from ..core.timing import Trace
from ..permissions.gate import ConfirmChoice, ConfirmRequest
from .endpoint import Endpointer, EpEvent
from .stt import Transcriber
from .tts import SentenceStream, Speaker
from .voicelock import VoiceLock
from .wakeword import WakeWord, ascii_upper
from .yesno import parse_answer

log = logging.getLogger(__name__)

PRE_ROLL_S = 1.6       # audio kept from before the trigger: contains the name (double-check) and early words
PARTIAL_EVERY_S = 0.8  # live transcript refresh while you speak
CONFIRM_WAIT_S = 7.0   # how long to wait for a spoken yes/no
LEAD_WORDS = {"hey", "hi", "ok", "okay", "hallo", "he", "hej", "na", "so", "also", "ja"}


class VState(StrEnum):
    OFF = "off"
    IDLE = "idle"
    LISTENING = "listening"
    TRANSCRIBING = "transcribing"
    BUSY = "busy"            # assistant is thinking / acting
    SPEAKING = "speaking"
    FOLLOW_UP = "follow_up"  # listening without the name for a few seconds after an answer


def _words(text: str) -> list[str]:
    return re.findall(r"[^\W_]+", text.casefold())


def name_position(text: str, name: str, variants: list[str], min_ratio: int = 85) -> int:
    """Index of the word right after the wake name at the start of ``text``, or -1."""
    from rapidfuzz import fuzz

    keys = {ascii_upper(n).replace(" ", "").lower() for n in [name, *variants] if n}
    words = _words(text)
    i = 0
    while i < len(words) - 1 and words[i] in LEAD_WORDS:
        i += 1
    for j in range(i, min(i + 3, len(words))):
        for span in (1, 2):
            cand = ascii_upper("".join(words[j:j + span])).replace(" ", "").lower()
            if cand and max(fuzz.ratio(cand, k) for k in keys) >= min_ratio:
                return j + span
    return -1


def strip_name(text: str, name: str, variants: list[str]) -> str:
    """'Hey Jarvis, open Spotify.' -> 'open Spotify.' (keeps the original spelling of the rest)."""
    pos = name_position(text, name, variants, 85)
    if pos < 0:
        return text.strip()
    matches = list(re.finditer(r"[^\W_]+", text))
    if pos >= len(matches):
        return ""
    return text[matches[pos].start():].strip(" ,.;:!?-")


@dataclass
class _Session:
    gen: int
    kind: str                 # wake | ptt | ptt_hold | follow_up | wake2 (second step after a bare name)
    trace: Trace
    started: float
    audio: list[np.ndarray]
    pre: int                  # samples of pre-roll at the start of ``audio``
    result: queue.Queue[np.ndarray | None] | None = None   # "confirm" sessions hand their audio back here


class VoicePipeline:
    def __init__(self, ctx: AppContext, assistant: Any, *, source_factory: Callable[[], AudioSource],
                 wake: WakeWord, endpointer: Endpointer, stt: Transcriber, speaker: Speaker | None,
                 voicelock: VoiceLock | None = None):
        self.ctx = ctx
        self.assistant = assistant
        self.source_factory = source_factory
        self.wake = wake
        self.ep = endpointer
        self.stt = stt
        self.speaker = speaker
        self.voicelock = voicelock
        self.ring = RingBuffer(4.0)
        self.state = VState.OFF
        self._lock = threading.RLock()
        self._gen = 0
        self._session: _Session | None = None
        self._source: AudioSource | None = None
        self._thread: threading.Thread | None = None
        self._running = False
        self._partial_busy = False
        self._last_partial = 0.0
        self._last_level = 0.0
        self._housekeeping = time.monotonic()
        self._silent_s = 0.0          # how long the microphone has delivered nothing but exact zeros
        self._heard_sound = False
        self.current_cancel: CancelToken | None = None
        self._answering: int | None = None    # generation of the voice request being answered
        self.requests: queue.Queue[tuple[str, str]] = queue.Queue()   # (kind, text) - for the harness/tests

    # ------------------------------------------------------------------ helpers
    @property
    def running(self) -> bool:
        """True while the microphone is open and the wake word is being listened for."""
        return self._running

    @property
    def cfg(self):  # type: ignore[no-untyped-def]
        return self.ctx.config.data.voice

    def _set(self, state: VState, **extra: Any) -> None:
        self.state = state
        phase = {VState.LISTENING: Phase.LISTENING, VState.FOLLOW_UP: Phase.LISTENING,
                 VState.TRANSCRIBING: Phase.THINKING, VState.BUSY: Phase.THINKING,
                 VState.SPEAKING: Phase.SPEAKING}.get(state, Phase.IDLE)
        self.ctx.state.update(phase=phase)
        self.ctx.bus.publish("voice.state", state=str(state), **extra)

    def _chime(self, name: str) -> None:
        if self.cfg.chime and self.speaker is not None:
            try:
                self.speaker.player.play(sound(name))
            except Exception as e:
                log.debug("chime failed: %s", e)

    # ------------------------------------------------------------------ lifecycle
    def start(self) -> None:
        """Open the microphone and start listening. Raises if the microphone can't be opened."""
        if self._running:
            return
        src = self.source_factory()
        src.start()
        self._source = src
        self._silent_s, self._heard_sound = 0.0, False
        self._running = True
        self._set(VState.IDLE)
        self.ctx.state.update(voice_ready=True)
        self._thread = threading.Thread(target=self._audio_loop, name="voice-audio", daemon=True)
        self._thread.start()
        log.info("voice pipeline started (wake word %r)", self.wake.name)

    def stop(self) -> None:
        self._running = False
        self.cancel("voice stopped")
        if self._source is not None:
            self._source.stop()
            self._source = None
        if self._thread is not None:
            self._thread.join(3)
            self._thread = None
        self._set(VState.OFF)
        self.ctx.state.update(voice_ready=False)

    def set_paused(self, paused: bool) -> None:
        """Pause = microphone closed (privacy), not just ignored."""
        if paused and self._running:
            self.stop()
        elif not paused and not self._running:
            self.start()

    def join(self, timeout: float | None = None) -> None:
        if self._thread is not None:
            self._thread.join(timeout)

    # ------------------------------------------------------------------ external triggers
    def push_to_talk(self, hold: bool = False) -> None:
        with self._lock:
            if self.state is VState.OFF:
                return
            self._interrupt()
            self._begin("ptt_hold" if hold else "ptt", pre_roll=0.25)

    def ptt_press(self, hold: bool = False) -> None:
        """Hotkey pressed. Press mode: a second press while listening ends the request at once."""
        with self._lock:
            s = self._session
            if not hold and s is not None and s.kind == "ptt" and self.state is VState.LISTENING:
                self._finish(s)
                return
        self.push_to_talk(hold)

    def ptt_release(self) -> None:
        with self._lock:
            s = self._session
            if s is not None and s.kind == "ptt_hold" and self.state is VState.LISTENING:
                self._finish(s)

    def cancel(self, reason: str = "cancelled") -> None:
        """Panic: stop speaking, cancel the request, release mouse/keyboard, back to idle."""
        with self._lock:
            self._gen += 1
            self._session = None
            if self.speaker is not None:
                self.speaker.stop()
            if self.current_cancel is not None:
                self.current_cancel.cancel(reason)
            try:
                self.assistant.cancel(reason)
            except Exception:
                log.exception("assistant cancel failed")
            if self._running:
                self._set(VState.IDLE)
                self.wake.reset()

    def _interrupt(self) -> None:
        """Barge-in: stop speaking and forget the follow-up window (the running request continues)."""
        if self.speaker is not None and self.state in (VState.SPEAKING, VState.BUSY, VState.FOLLOW_UP):
            self.speaker.stop()

    # ------------------------------------------------------------------ audio thread
    def _audio_loop(self) -> None:
        _eco_qos(self.ctx.config.data.performance.eco_qos)
        src = self._source
        assert src is not None
        while self._running:
            try:
                frame = src.frames.get(timeout=0.5)
            except queue.Empty:
                if not getattr(src, "alive", True):
                    reopened = self._reopen(src)
                    if reopened is None:
                        break
                    src = reopened
                self._house()
                continue
            if frame is None:
                break
            if not self._heard_sound:
                self._check_silence(frame)
            self.ring.write(frame)
            self._publish_level(src)
            with self._lock:
                st = self.state
                if st in (VState.IDLE, VState.BUSY, VState.SPEAKING, VState.TRANSCRIBING):
                    if self.cfg.wake_word_enabled and self.wake.process(frame):
                        self._on_wake()
                elif st in (VState.LISTENING, VState.FOLLOW_UP) and self._session is not None:
                    self._listen_frame(self._session, frame)
            self._house()
        log.debug("voice audio loop ended")
        if self._running:   # source ended (WAV harness)
            self._running = False

    SILENT_MIC_S = 6.0

    def _check_silence(self, frame: np.ndarray) -> None:
        """A real microphone always has a little noise. Exact zeros mean Windows blocks it (privacy setting) or a
        muted/virtual device: say how to fix it, once. Costs nothing once any sound was heard."""
        if frame.size and float(np.max(np.abs(frame))) > 1e-6:
            self._heard_sound = True
            return
        self._silent_s += frame.size / RATE
        if self.SILENT_MIC_S <= self._silent_s < self.SILENT_MIC_S + frame.size / RATE:
            log.warning("the microphone delivers only silence")
            de = self.ctx.config.data.general.language == "de"
            self.ctx.bus.publish("ui.notify", title="Mikrofon" if de else "Microphone", level="warning", message=(
                "Das Mikrofon liefert nur Stille. Windows-Einstellungen → Datenschutz und Sicherheit → Mikrofon: "
                "„Desktop-Apps Zugriff auf das Mikrofon gewähren“ einschalten - oder unter Einstellungen → Stimme "
                "ein anderes Mikrofon wählen." if de else
                "The microphone only delivers silence. In Windows Settings → Privacy & security → Microphone, turn on "
                "“Let desktop apps access your microphone” - or choose another microphone in Settings → Voice."))

    def _reopen(self, old: AudioSource) -> AudioSource | None:
        """The microphone died (unplugged, driver reset): keep trying to open it again."""
        log.warning("microphone stopped delivering audio; reopening")
        self.ctx.bus.publish("voice.mic_lost")
        try:
            old.stop()
        except Exception:
            pass
        delay, told = 1.0, False
        while self._running:
            try:
                new = self.source_factory()
                new.start()
                if not self._running:      # stopped meanwhile: don't leave a microphone open
                    new.stop()
                    return None
                self._source = new
                self._silent_s, self._heard_sound = 0.0, False
                log.info("microphone reopened")
                self.ctx.bus.publish("voice.mic_back")
                return new
            except Exception as e:
                if not told:
                    told = True
                    self.ctx.bus.publish("ui.notify", title="Microphone", level="warning",
                                         message=f"No microphone available ({e}). Retrying in the background.")
                time.sleep(delay)
                delay = min(delay * 2, 10.0)
        return None

    def _house(self) -> None:
        now = time.monotonic()
        if now - self._housekeeping > 30:
            self._housekeeping = now
            if self.state is VState.IDLE:
                self.stt.unload_if_idle()
                if self.speaker is not None:
                    self.speaker.unload_if_idle()

    def _publish_level(self, src: AudioSource) -> None:
        """~15/s while listening (mic) or speaking (our own voice) - drives the orb."""
        now = time.monotonic()
        if now - self._last_level < 0.066:
            return
        if self.state in (VState.LISTENING, VState.FOLLOW_UP):
            level = getattr(src, "level", 0.0)
        elif self.state is VState.SPEAKING and self.speaker is not None:
            level = getattr(self.speaker.player, "level", 0.0)
        else:
            return
        self._last_level = now
        self.ctx.bus.publish("voice.level", level=level)

    def _on_wake(self) -> None:
        if self.state is VState.SPEAKING and self.speaker is not None and \
                self.wake.name.casefold() in self.speaker.speaking_text.casefold():
            log.debug("ignoring wake word inside our own speech output")
            return
        log.info("wake word detected")
        self._interrupt()
        self._begin("wake", pre_roll=PRE_ROLL_S)

    def _begin(self, kind: str, pre_roll: float) -> None:
        self._gen += 1
        trace = Trace("voice")
        trace.mark("wake")
        pre = self.ring.last(pre_roll)
        self._session = _Session(self._gen, kind, trace, time.monotonic(), [pre], len(pre))
        self.ep.configure(self.cfg.silence_timeout_s, 5.0, self.cfg.max_utterance_s)
        self.ep.reset()
        if kind == "wake":       # prime the VAD with the name so it knows speech is under way
            for i in range(0, len(pre), 512):
                self.ep.feed(pre[i:i + 512])
            self.ep.t = 0.0
            self.ep.last_speech = 0.0 if self.ep.spoke else -1.0
        self.stt.preload()
        if self.speaker is not None and self.cfg.tts_enabled:
            self.speaker.preload()
        self._set(VState.LISTENING, kind=kind)
        self.ctx.bus.publish("voice.wake", kind=kind, gen=self._gen)
        if kind != "wake2":
            self._chime("wake")

    def _listen_frame(self, s: _Session, frame: np.ndarray) -> None:
        s.audio.append(frame)
        ev = self.ep.feed(frame)
        if ev is EpEvent.SPEECH and self.state is VState.FOLLOW_UP:
            self._set(VState.LISTENING, kind="follow_up")
        if ev is EpEvent.MAX or (ev is EpEvent.END and s.kind != "ptt_hold"):   # hold-to-talk ends on release
            self._finish(s)
        elif ev is EpEvent.NO_SPEECH:
            self._session = None
            if s.result is not None:          # spoken confirmation: nothing said
                self._set(VState.BUSY)
                s.result.put(None)
                return
            if s.kind in ("follow_up",):
                self._set(VState.IDLE)
            else:
                log.info("no speech after %s", s.kind)
                self._chime("error")
                self.ctx.bus.publish("voice.nothing_heard", kind=s.kind)
                self._set(VState.IDLE)
            self.wake.reset()
        elif s.result is None and self.cfg.live_transcript and self.stt.loaded and not self._partial_busy and \
                time.monotonic() - self._last_partial >= PARTIAL_EVERY_S and self.ep.spoke and \
                self.ep.t - self.ep.last_speech < 0.25:   # not when the end is near: the final pass must not wait
            self._last_partial = time.monotonic()
            self._partial_busy = True
            audio = np.concatenate(s.audio)
            threading.Thread(target=self._partial, args=(s.gen, audio), name="stt-partial", daemon=True).start()

    def _partial(self, gen: int, audio: np.ndarray) -> None:
        try:
            text = self.stt.transcribe(audio)
            if gen == self._gen and self.state is VState.LISTENING and text:
                self.ctx.bus.publish("voice.partial", text=text, gen=gen)
        except Exception as e:
            log.debug("partial transcript failed: %s", e)
        finally:
            self._partial_busy = False

    def _finish(self, s: _Session) -> None:
        self._session = None
        s.trace.mark("speech_end")
        audio = np.concatenate(s.audio)
        if s.result is not None:              # spoken confirmation: the waiting request transcribes it
            self._set(VState.BUSY)
            s.result.put(audio)
            return
        self._set(VState.TRANSCRIBING)
        self.wake.reset()
        threading.Thread(target=self._process, args=(s, audio), name="voice-request", daemon=True).start()

    # ------------------------------------------------------------------ request thread
    def _process(self, s: _Session, audio: np.ndarray) -> None:
        cfg = self.cfg
        name = self.ctx.config.data.general.assistant_name
        variants = list(cfg.wake_variants)
        if not self.stt.loaded:
            self.ctx.bus.publish("voice.status", text="loading speech recognition")
        try:
            text = self.stt.transcribe(audio)
        except Exception:
            log.exception("transcription failed")
            text = ""
        s.trace.mark("transcript")
        if s.gen != self._gen:
            return
        log.info("heard (%s): %s", s.kind,
                 text if self.ctx.config.data.privacy.log_transcripts else f"<{len(text)} chars>")
        if self.voicelock is not None and cfg.voice_lock and self.voicelock.enrolled:
            ok, score = self.voicelock.verify(audio)
            log.info("voice lock: %s (score %.2f)", "accepted" if ok else "rejected", score)
            if not ok:
                self.ctx.bus.publish("voice.rejected", score=score)
                self._back_to_idle(s)
                return
        if self.ctx.config.data.privacy.save_audio:      # off by default: audio normally never leaves memory
            from .recordings import save

            save(self.ctx, audio, s.kind)
        command = strip_name(text, name, variants)
        if s.kind == "wake":
            if cfg.wake_double_check and text and name_position(text, name, variants) < 0:
                log.info("wake word not confirmed by speech recognition -> ignored")
                self.ctx.bus.publish("voice.false_trigger", text=text)
                self._back_to_idle(s)
                return
            if not command:          # just the name: wait for the actual request
                with self._lock:
                    if s.gen == self._gen:
                        self._begin("wake2", pre_roll=0.2)
                return
        if not command.strip() or (s.kind == "follow_up" and len(_words(command)) < 2):
            self._back_to_idle(s)
            return
        self.ctx.bus.publish("voice.transcript", text=command, kind=s.kind)
        self.requests.put((s.kind, command))
        self._answer(s, command)

    def _answer(self, s: _Session, command: str) -> None:
        cfg = self.cfg
        lang = self.ctx.config.data.general.language
        speak = cfg.tts_enabled and self.speaker is not None
        cancel = CancelToken()
        self.current_cancel = cancel
        spoken: list[str] = []

        def say(sentence: str) -> None:
            if s.gen == self._gen and not cancel.cancelled and self.speaker is not None:
                spoken.append(sentence)
                if self.state is not VState.SPEAKING:
                    self._set(VState.SPEAKING)
                self.speaker.say(sentence, lang, cfg.tts_voice_de if lang == "de" else cfg.tts_voice_en,
                                 cfg.tts_speed, cancel)

        stream = SentenceStream(say) if speak else None
        self._set(VState.BUSY)
        if self.speaker is not None:
            self.speaker.first_audio_at = None
        t_reply = time.perf_counter()
        self._answering = s.gen
        try:
            reply = self.assistant.handle(command, source="voice", on_content=stream.feed if stream else None)
        finally:
            self._answering = None
        s.trace.mark("reply")
        if s.gen != self._gen:
            return
        if stream is not None:
            stream.flush()
            rest = reply.text
            done = " ".join(spoken)
            if done and rest.startswith(done):
                rest = rest[len(done):]
            elif done:
                rest = ""   # streamed text and final text differ (e.g. tool preamble): keep what was said
            if rest.strip():
                say(rest.strip())
            if self.speaker is not None:
                self.speaker.wait(cancel)
                if self.speaker.first_audio_at:
                    log.debug("TIMING stage=reply_to_first_audio ms=%.1f",
                              (self.speaker.first_audio_at - t_reply) * 1000)
        s.trace.finish((("wake", "speech_end"), ("speech_end", "transcript"), ("transcript", "reply")))
        if s.gen != self._gen:
            return
        with self._lock:
            if s.gen != self._gen:
                return
            if cfg.follow_up and reply.via not in ("cancelled",) and reply.intent != "stop":
                self._session = _Session(self._gen, "follow_up", Trace("voice"), time.monotonic(), [], 0)
                self._session.trace.mark("wake")
                self.ep.configure(cfg.silence_timeout_s, cfg.follow_up_s, cfg.max_utterance_s)
                self.ep.reset()
                self._set(VState.FOLLOW_UP)
            else:
                self._set(VState.IDLE)
                self.wake.reset()

    # ------------------------------------------------------------------ spoken permission answers
    def can_confirm_by_voice(self) -> bool:
        """True while a *voice* request is being answered (typed requests are confirmed by click only)."""
        return self._running and self._answering is not None and self._answering == self._gen

    def confirm_by_voice(self, request: ConfirmRequest, stop: threading.Event) -> ConfirmChoice | None:
        """Ask the permission question out loud and listen for yes / no (runs beside the click buttons;
        ``stop`` is set once either answered). Returns None when there is no clear spoken answer."""
        gen = self._answering
        if gen is None or gen != self._gen or not self._running:
            return None
        lang = self.ctx.config.data.general.language
        what = request.action.summary.strip().rstrip(".?!")
        questions = {"en": (f"{what}? Say yes or no.", "Sorry, yes or no?"),
                     "de": ("Darf ich das ausführen? Sag ja oder nein.", "Ja oder nein?")}[
            "de" if lang == "de" else "en"]
        for attempt in range(2):
            if stop.is_set() or gen != self._gen:
                return None
            self._speak_and_wait(questions[attempt], gen, stop)
            audio = self._listen_once(gen, stop)
            if audio is None:
                return None
            text = self.stt.transcribe(audio)
            if stop.is_set() or gen != self._gen:
                return None
            if self.voicelock is not None and self.cfg.voice_lock and self.voicelock.enrolled:
                ok, score = self.voicelock.verify(audio)
                if not ok:
                    log.info("spoken confirmation rejected by voice lock (score %.2f)", score)
                    self.ctx.bus.publish("voice.rejected", score=score)
                    continue
            name = self.ctx.config.data.general.assistant_name
            answer = parse_answer(strip_name(text, name, list(self.cfg.wake_variants)))
            log.info("spoken confirmation: %s", answer if answer is not None else "unclear")
            if answer == "stop":
                self.cancel("stop")
                return ConfirmChoice.DENY
            if isinstance(answer, ConfirmChoice):
                return answer
        return None

    def _speak_and_wait(self, text: str, gen: int, stop: threading.Event) -> None:
        if self.speaker is None or not self.cfg.tts_enabled:
            return
        tok = (self.current_cancel or CancelToken()).child()

        def wait() -> bool:   # False if the question was answered by click / cancelled meanwhile
            assert self.speaker is not None
            while not self.speaker.wait(tok, timeout=0.1):
                if tok.cancelled:
                    return False
                if stop.is_set() or gen != self._gen:
                    tok.cancel("answered")
                    self.speaker.stop()
                    return False
            return True

        if not wait():                          # let a sentence that is already playing finish
            return
        lang = self.ctx.config.data.general.language
        with self._lock:
            if gen != self._gen:
                return
            self._set(VState.SPEAKING)
        self.speaker.say(text, lang, self.cfg.tts_voice_de if lang == "de" else self.cfg.tts_voice_en,
                         self.cfg.tts_speed, tok)
        wait()

    def _listen_once(self, gen: int, stop: threading.Event) -> np.ndarray | None:
        with self._lock:
            if gen != self._gen or not self._running:
                return None
            s = _Session(gen, "confirm", Trace("voice"), time.monotonic(), [], 0, queue.Queue(maxsize=1))
            self._session = s
            self.ep.configure(self.cfg.silence_timeout_s, CONFIRM_WAIT_S, 8.0)
            self.ep.reset()
            self.stt.preload()
            self._set(VState.LISTENING, kind="confirm")
            self._chime("wake")
        assert s.result is not None
        while True:
            try:
                return s.result.get(timeout=0.2)
            except queue.Empty:
                if stop.is_set() or gen != self._gen or not self._running:
                    with self._lock:
                        if self._session is s:
                            self._session = None
                            if self._running and gen == self._gen:
                                self._set(VState.BUSY)
                    return None

    def _back_to_idle(self, s: _Session) -> None:
        with self._lock:
            if s.gen == self._gen:
                self._set(VState.IDLE)
                self.wake.reset()


def _eco_qos(enabled: bool) -> None:
    if not enabled:
        return
    try:
        from ..win.qos import set_thread_eco_qos

        set_thread_eco_qos(True)
    except Exception as e:  # pragma: no cover
        log.debug("EcoQoS not applied: %s", e)


__all__ = ["RATE", "VState", "VoicePipeline", "name_position", "strip_name"]
