"""End-to-end voice pipeline tests with the real speech models (skipped when they aren't installed).

Test audio is generated with the assistant's own text-to-speech, fed through the
pipeline like a microphone (ArraySource), and the answers go to a fake assistant.
Models are looked up in ASSISTANT_TEST_VOICE_DIR or the normal install location.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from typing import Any

import numpy as np
import pytest

from assistant.audio.player import NullPlayer
from assistant.audio.sources import RATE, ArraySource
from assistant.core.assistant import Reply
from assistant.core.paths import resolve_paths
from assistant.permissions.gate import Action, ConfirmChoice, ConfirmRequest
from assistant.voice.models import CORE_VOICE, VOICE_MODELS


def _find_models() -> Path | None:
    for d in (os.environ.get("ASSISTANT_TEST_VOICE_DIR"), resolve_paths(None).models_dir / "voice"):
        if d and all((Path(d) / VOICE_MODELS[m].check).exists() for m in (*CORE_VOICE, "speaker")):
            return Path(d)
    return None


MODELS = _find_models()
pytestmark = pytest.mark.skipif(MODELS is None, reason="voice models not installed (run setup)")


def _link(src: Path, dst: Path) -> None:
    try:
        os.symlink(src, dst, target_is_directory=True)
    except OSError:
        if sys.platform != "win32":
            raise
        import _winapi  # junctions need no admin rights

        _winapi.CreateJunction(str(src), str(dst))


class FakeAssistant:
    def __init__(self, answer: str = "It is ten o'clock."):
        self.answer = answer
        self.requests: list[str] = []
        self.cancelled: list[str] = []
        self.pipeline: Any = None
        self.choice: Any = None

    def handle(self, text: str, *, source: str = "text", on_content: Any = None) -> Reply:
        self.requests.append(text)
        if on_content is not None:
            on_content(self.answer)
        return Reply(self.answer, via="fast")

    def cancel(self, reason: str = "stop") -> bool:
        self.cancelled.append(reason)
        return False


class ConfirmingAssistant(FakeAssistant):
    """Like a tool that needs permission: asks by voice in the middle of the request."""

    def handle(self, text: str, *, source: str = "text", on_content: Any = None) -> Reply:
        self.requests.append(text)
        req = ConfirmRequest(action=Action(tool="files", capability="files.delete", summary="Delete the old report"),
                             reasons=[], allow_task=False, allow_always=False)
        assert self.pipeline.can_confirm_by_voice()
        self.choice = self.pipeline.confirm_by_voice(req, threading.Event())
        return Reply("Deleted." if self.choice is ConfirmChoice.ONCE else "Okay, I left it.", via="fast")


@pytest.fixture(scope="module")
def voice(tmp_path_factory):
    """Shared models (loading Parakeet takes seconds) + a context with its own home."""
    from assistant.core.context import bootstrap
    from assistant.voice.endpoint import Endpointer
    from assistant.voice.runtime import model_path
    from assistant.voice.stt import Transcriber
    from assistant.voice.tts import Speaker
    from assistant.voice.voicelock import VoiceLock
    from assistant.voice.wakeword import WakeWord

    assert MODELS is not None
    home = tmp_path_factory.mktemp("voicehome")
    (home / "models").mkdir()
    _link(MODELS, home / "models" / "voice")
    ctx = bootstrap("cli", home=str(home), detect_hardware="off")
    ctx.config.update({"general.assistant_name": "Jarvis", "voice.chime": False})
    parts = {
        "wake": WakeWord(model_path(ctx, "kws"), "Jarvis", keywords_file=ctx.paths.cache_dir / "kw.txt"),
        "ep": Endpointer(model_path(ctx, "vad")),
        "stt": Transcriber(model_path(ctx, "stt")),
        "speaker": Speaker(model_path(ctx, "tts"), NullPlayer()),
        "lock": VoiceLock(model_path(ctx, "speaker"), home / "voiceprint.npy"),
    }
    cache: dict[tuple[str, str], np.ndarray] = {}

    def say(text: str, voice_id: str = "M1", lang: str = "en") -> np.ndarray:
        """Synthesized speech at 16 kHz with a little silence around it."""
        key = (text, voice_id)
        if key not in cache:
            import soxr

            samples, sr = parts["speaker"].synthesize(text, lang, voice_id)
            x = soxr.resample(samples, sr, RATE).astype(np.float32)
            pad = np.zeros(int(0.3 * RATE), np.float32)
            cache[key] = np.concatenate([pad, x, pad])
        return cache[key]

    return ctx, parts, say


def _pipeline(voice, assistant, *clips, realtime=False, gap_s=2.5, voicelock=False):
    from assistant.voice.pipeline import VoicePipeline

    ctx, p, _ = voice
    p["wake"].configure("Jarvis", 0.5, [])
    pipe = VoicePipeline(ctx, assistant, source_factory=lambda: ArraySource(*clips, realtime=realtime, gap_s=gap_s,
                                                                             tail_s=3.0 if realtime else 2.0),
                         wake=p["wake"], endpointer=p["ep"], stt=p["stt"], speaker=p["speaker"],
                         voicelock=p["lock"] if voicelock else None)
    assistant.pipeline = pipe
    return pipe


def _run(pipe, timeout: float = 90.0) -> list:
    events: list = []
    unsub = pipe.ctx.bus.subscribe("*", lambda e: events.append(e) if e.topic.startswith("voice.")
                                   and e.topic != "voice.level" else None)
    try:
        pipe.start()
        deadline = time.time() + timeout
        while pipe.running and time.time() < deadline:
            time.sleep(0.05)
        while time.time() < deadline and pipe.state.value not in ("idle", "off", "follow_up"):
            time.sleep(0.05)
        time.sleep(0.2)
    finally:
        pipe.stop()
        unsub()
    return events


def _topics(events) -> list[str]:
    return [e.topic for e in events]


def test_wake_word_and_command(voice):
    _, _, say = voice
    a = FakeAssistant()
    pipe = _pipeline(voice, a, say("Jarvis, what time is it?"))
    events = _run(pipe)
    assert len(a.requests) == 1, _topics(events)
    assert "what time is it" in a.requests[0].lower()
    kinds = [e.data.get("kind") for e in events if e.topic == "voice.wake"]
    assert kinds == ["wake"]
    spoken = pipe.speaker.player.played  # NullPlayer: the answer was synthesized
    assert spoken and sum(len(x) for x in spoken) > 0.5 * 44100


def test_no_name_no_request(voice):
    _, _, say = voice
    a = FakeAssistant()
    events = _run(_pipeline(voice, a, say("What time is it? Play some music please.")))
    assert a.requests == [] and "voice.wake" not in _topics(events)


def test_double_check_rejects_a_false_trigger(voice):
    """The keyword spotter fires by mistake; the transcript has no name -> ignored."""
    ctx, p, say = voice
    a = FakeAssistant()
    pipe = _pipeline(voice, a, say("Please open the calendar for tomorrow."))
    real = p["wake"].process
    fired = {"n": 0}

    def process(frame):   # fire once, ~0.6 s into the audio (during the speech)
        fired["n"] += 1
        return fired["n"] == 30 or real(frame)

    p["wake"].process = process
    try:
        events = _run(pipe)
    finally:
        del p["wake"].process
    assert a.requests == []
    assert "voice.false_trigger" in _topics(events)


def test_two_step_name_then_command(voice):
    _, _, say = voice
    a = FakeAssistant()
    events = _run(_pipeline(voice, a, say("Jarvis."), say("Open the calculator."), realtime=True, gap_s=2.6))
    assert len(a.requests) == 1, _topics(events)
    assert "calculator" in a.requests[0].lower()
    assert [e.data.get("kind") for e in events if e.topic == "voice.wake"] == ["wake", "wake2"]


def test_follow_up_without_the_name(voice):
    _, _, say = voice
    a = FakeAssistant(answer="Ten o'clock.")
    events = _run(_pipeline(voice, a, say("Jarvis, what time is it?"), say("And what is the date today?"),
                            realtime=True, gap_s=2.4))
    assert len(a.requests) == 2, (a.requests, _topics(events))
    assert "date" in a.requests[1].lower()
    kinds = [e.data.get("kind") for e in events if e.topic == "voice.transcript"]
    assert kinds == ["wake", "follow_up"]


def test_spoken_confirmation(voice):
    _, _, say = voice
    a = ConfirmingAssistant()
    events = _run(_pipeline(voice, a, say("Jarvis, delete the old report."), say("Yes, go ahead."),
                            realtime=True, gap_s=3.2))
    assert len(a.requests) == 1, _topics(events)
    assert a.choice is ConfirmChoice.ONCE, _topics(events)
    assert any(e.topic == "voice.state" and e.data.get("kind") == "confirm" for e in events)


def test_spoken_refusal(voice):
    _, _, say = voice
    a = ConfirmingAssistant()
    _run(_pipeline(voice, a, say("Jarvis, delete the old report."), say("No, don't do that."),
                   realtime=True, gap_s=3.2))
    assert a.choice is ConfirmChoice.DENY


def test_voice_lock_tells_speakers_apart(voice, tmp_path):
    _, p, say = voice
    lock = p["lock"]
    lock.profile = tmp_path / "vp.npy"
    lock.enroll([say("The quick brown fox jumps over the lazy dog.", "M1"),
                 say("What is the weather like tomorrow in Berlin?", "M1"),
                 say("Open my documents folder and play some music.", "M1")])
    ok_same, s_same = lock.verify(say("Remind me to call my sister at six.", "M1"))
    ok_other, s_other = lock.verify(say("Remind me to call my sister at six.", "F1"))
    assert ok_same and not ok_other, (s_same, s_other)
    assert s_same > s_other + 0.2
    lock.forget()


def test_calibration_finds_the_name(voice):
    from assistant.voice.calibration import calibrate_wake_word

    ctx, p, say = voice
    clips = [say("Jarvis.", v) for v in ("M1", "F1", "M3")]
    res = calibrate_wake_word(ctx, p["wake"], p["stt"], clips, save=False)
    assert res.ok and res.detected >= 2
    assert any("jarvis" in h.lower() for h in res.heard)
    p["wake"].configure("Jarvis", 0.5, [])


# ---------------------------------------------------------------------- setup wizard speech (milestone 5)
def _setup_voice(voice, clips, until):
    from assistant.onboarding.voice import SetupVoice

    ctx, p, _ = voice
    events: list = []
    done = threading.Event()
    it = iter(clips)

    def on(kind, data):
        events.append((kind, data))
        if kind in until or kind == "error":
            done.set()

    sv = SetupVoice(ctx, on, source_factory=lambda: ArraySource(next(it), tail_s=2.0), player=NullPlayer())
    sv._stt = p["stt"]                    # share the loaded model with the other tests
    return sv, events, done


def _close(sv):
    sv._stt = None                        # don't unload the shared model
    sv.close()


def test_setup_voice_hears_an_answer(voice):
    _, _, say = voice
    # (the synthesized voice varies per run; "Lennox" came out as "Linux" half of the time, "Michael" never fails)
    sv, events, done = _setup_voice(voice, [say("Call me Michael.")], ("heard", "nothing"))
    sv.ask("", speak=False, listen=True)
    assert done.wait(60)
    _close(sv)
    heard = [d["text"] for k, d in events if k == "heard"]
    assert heard and "michael" in heard[0].lower(), events
    assert ("listening", {}) in events


def test_setup_voice_calibrates_the_name(voice):
    _, _, say = voice
    sv, events, done = _setup_voice(voice, [say("Jarvis.", v) for v in ("M1", "F1", "M2")], ("calibrated",))
    sv.record_name("Jarvis")
    assert done.wait(60)
    _close(sv)
    result = next(d["result"] for k, d in events if k == "calibrated")
    assert result["ok"] and result["detected"] >= 2 and result["total"] == 3
    assert [d["n"] for k, d in events if k == "take"] == [1, 2, 3]


def test_setup_voice_enrolls_the_voice_lock(voice):
    ctx, _, say = voice
    sentences = ["The quick brown fox jumps over the lazy dog.", "What's the weather like tomorrow?",
                 "Please open my documents folder and play some music."]
    sv, events, done = _setup_voice(voice, [say(s, "M1") for s in sentences], ("enrolled",))
    sv.enroll("en")
    assert done.wait(60)
    _close(sv)
    profile = ctx.paths.config_dir / "voiceprint.npy"
    assert ("enrolled", {}) in events and profile.exists()
    profile.unlink()


def test_the_voice_is_evened_out_and_follows_the_volume_setting(tmp_path):
    """Real speech output: every sentence reaches ~-1 dBFS peaks (the raw voice is ~-26 dBFS RMS, far quieter than
    music), and Settings → Voice volume makes it louder still - softly limited, never clipped."""
    from assistant.core.cancel import CancelToken
    from assistant.core.context import bootstrap
    from assistant.voice.runtime import build_voice

    home = tmp_path / "home"
    (home / "models").mkdir(parents=True)
    _link(MODELS, home / "models" / "voice")
    ctx = bootstrap("cli", home=str(home), detect_hardware="off")
    player = NullPlayer()
    stack = build_voice(ctx, None, player=player, headless=True)
    sp = stack.speaker
    assert sp.volume == 1.0

    def speak(text):
        player.played.clear()
        sp.say(text, "en", "M1", 1.0, CancelToken())
        assert sp.wait(timeout=60)
        return np.concatenate(player.played)

    raw, sr = sp.synthesize("Gaming mode activated. All systems online.", "en", "M1")
    normal = speak("Gaming mode activated. All systems online.")
    assert float(np.abs(normal).max()) == pytest.approx(0.89, abs=0.02)
    rms = lambda x: float(np.sqrt(np.mean(x ** 2)))  # noqa: E731
    assert 20 * np.log10(rms(normal) / rms(raw)) > 2              # clearly louder than the raw voice
    ctx.config.set("voice.tts_volume", 160)                        # Settings → Voice volume 160 %
    assert sp.volume == 1.6
    loud = speak("Gaming mode activated. All systems online.")
    assert rms(loud) > rms(normal) * 1.25 and float(np.abs(loud).max()) < 0.98
