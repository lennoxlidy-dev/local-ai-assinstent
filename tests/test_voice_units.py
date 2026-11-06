"""Milestone 4 unit tests that need no models: text helpers, yes/no, hotkeys, audio buffers, popup logic."""

from __future__ import annotations

import threading
import time

import numpy as np
import pytest

from assistant.audio.player import NullPlayer
from assistant.audio.sources import RATE, ArraySource, RingBuffer
from assistant.permissions.gate import Action, ConfirmChoice, ConfirmRequest
from assistant.voice.pipeline import name_position, strip_name
from assistant.voice.tts import SentenceStream, speakable, split_sentences
from assistant.voice.wakeword import ascii_upper, sensitivity_params
from assistant.voice.yesno import parse_answer
from assistant.win.hotkeys import HotkeyManager, parse_hotkey


# ---------------------------------------------------------------------- wake name in transcripts
@pytest.mark.parametrize("text,expected", [
    ("Jarvis, open Spotify.", "open Spotify"),
    ("Hey Jarvis what time is it?", "what time is it"),
    ("OK Jarvis, wie spät ist es", "wie spät ist es"),
    ("Javis, open notepad", "open notepad"),          # learned variant
    ("Jar vis play music", "play music"),             # split by the recognizer
    ("Jarvis.", ""),
    ("open Spotify", "open Spotify"),                 # no name: unchanged
    ("Davis, open Spotify", "Davis, open Spotify"),   # different name is not stripped
])
def test_strip_name(text, expected):
    assert strip_name(text, "Jarvis", ["Javis"]) == expected


def test_name_position():
    assert name_position("hey jarvis open it", "Jarvis", []) == 2
    assert name_position("what time is it", "Jarvis", []) == -1
    assert name_position("Jürgen, mach Musik an", "Jürgen", []) == 1


def test_ascii_upper_and_sensitivity():
    assert ascii_upper("Jürgen") == "JUERGEN"
    assert ascii_upper("Zoë-2") == "ZOE"
    assert ascii_upper("Straße") == "STRASSE"
    assert sensitivity_params(0.5) == (0.25, 1.5)
    t0, b0 = sensitivity_params(0.0)
    t1, b1 = sensitivity_params(1.0)
    assert t0 > t1 and b0 < b1
    assert sensitivity_params(7) == sensitivity_params(1.0)


# ---------------------------------------------------------------------- speech output text
def test_split_sentences_keeps_abbreviations_and_numbers():
    assert split_sentences("Hello there. How are you? Fine!") == ["Hello there.", "How are you?", "Fine!"]
    assert split_sentences("Das ist z.B. gut. Am 3. Mai geht es los.") == ["Das ist z.B. gut.", "Am 3. Mai geht es los."]


def test_speakable_strips_markdown_and_links():
    assert speakable("**Bold** and `code` see https://example.com/x") == "Bold and code see a link"


def test_sentence_stream_emits_complete_sentences():
    out: list[str] = []
    s = SentenceStream(out.append)
    for chunk in ["It is ", "ten o'clock. The wea", "ther is fine", " today. And"]:
        s.feed(chunk)
    assert out == ["It is ten o'clock.", "The weather is fine today."]
    s.flush()
    assert out[-1] == "And"


# ---------------------------------------------------------------------- spoken yes / no
@pytest.mark.parametrize("text,expected", [
    ("Yes.", ConfirmChoice.ONCE), ("yeah go ahead", ConfirmChoice.ONCE), ("Ja.", ConfirmChoice.ONCE),
    ("Ja, mach das", ConfirmChoice.ONCE), ("okay", ConfirmChoice.ONCE), ("In Ordnung.", ConfirmChoice.ONCE),
    ("No.", ConfirmChoice.DENY), ("Nein!", ConfirmChoice.DENY), ("no, don't", ConfirmChoice.DENY),
    ("yes... no wait", ConfirmChoice.DENY), ("Lieber nicht.", ConfirmChoice.DENY), ("don't do it", ConfirmChoice.DENY),
    ("Stop!", "stop"), ("abbrechen", "stop"), ("cancel that", "stop"),
    ("Yes, always.", ConfirmChoice.ALWAYS), ("immer erlauben", ConfirmChoice.ALWAYS),
    ("yes, for this task", ConfirmChoice.TASK),
    ("what?", None), ("", None), ("the weather is nice", None),
])
def test_parse_answer(text, expected):
    assert parse_answer(text) == expected


# ---------------------------------------------------------------------- hotkeys
def test_parse_hotkey():
    assert parse_hotkey("ctrl+alt+space") == (0x2 | 0x1, 0x20)
    assert parse_hotkey("Ctrl + Alt + Escape") == (0x3, 0x1B)
    assert parse_hotkey("win+shift+a") == (0x8 | 0x4, ord("A"))
    assert parse_hotkey("f9") == (0, 0x78)            # function keys may stand alone
    assert parse_hotkey("ctrl+f12") == (0x2, 0x7B)
    for bad in ("", "ctrl+alt", "a", "ctrl+a+b", "ctrl+nosuchkey"):
        with pytest.raises(ValueError):
            parse_hotkey(bad)


def test_hotkeys_unsupported_off_windows():
    m = HotkeyManager()
    if m.supported:
        pytest.skip("Windows: real registration is tested by hand")
    errors = m.start([])
    assert errors == {}
    m.stop()


# ---------------------------------------------------------------------- audio plumbing
def test_ring_buffer_returns_latest_audio():
    rb = RingBuffer(1.0)
    rb.write(np.arange(RATE // 2, dtype=np.float32))
    assert len(rb.last(2.0)) == RATE // 2               # only what was written
    rb.write(np.arange(RATE, 2 * RATE, dtype=np.float32))
    last = rb.last(0.25)
    assert len(last) == RATE // 4
    assert last[-1] == 2 * RATE - 1 and last[0] == 2 * RATE - RATE // 4
    big = np.arange(3 * RATE, dtype=np.float32)
    rb.write(big)
    assert np.array_equal(rb.last(1.0), big[-RATE:])


def test_array_source_feeds_frames_then_ends():
    src = ArraySource(np.ones(RATE, np.float32), gap_s=0.1, tail_s=0.2)
    src.start()
    total, frames = 0, 0
    while True:
        f = src.frames.get(timeout=2)
        if f is None:
            break
        total += len(f)
        frames += 1
    assert total == int(1.3 * RATE)
    assert frames > 10


def test_null_player_records():
    p = NullPlayer()
    p.play(np.ones(100, np.float32))
    p.play(np.zeros(50, np.float32))
    assert len(p.audio()) == 150 and not p.busy and p.wait()


# ---------------------------------------------------------------------- popup logic (Qt, offscreen)
@pytest.fixture
def popup(tmp_path):
    from PySide6.QtGui import QGuiApplication

    from assistant.core.context import bootstrap
    from assistant.ui.popup import PopupController

    app = QGuiApplication.instance() or QGuiApplication([])
    ctx = bootstrap("cli", home=str(tmp_path / "home"), detect_hardware="off")
    submitted: list[str] = []
    pc = PopupController(ctx, on_submit=submitted.append)
    pc.submitted_texts = submitted  # type: ignore[attr-defined]
    yield pc, ctx, app
    pc.close()


def _pump(app, ms: int = 30) -> None:
    end = time.monotonic() + ms / 1000
    while time.monotonic() < end:
        app.processEvents()
        time.sleep(0.005)


def test_popup_follows_a_voice_request(popup):
    pc, ctx, app = popup
    assert pc.create()
    bus, m = ctx.bus, pc.model
    bus.publish("voice.state", state="listening", kind="wake")
    bus.publish("voice.level", level=0.1)
    bus.publish("voice.partial", text="what time")
    _pump(app)
    assert pc.view.isVisible() and m.get("visibleState")
    assert (m.get("state"), m.get("transcript"), m.get("level")) == ("listening", "what time", 0.1)
    bus.publish("voice.state", state="transcribing")
    bus.publish("voice.transcript", text="what time is it")
    bus.publish("voice.state", state="busy")
    bus.publish("assistant.request", text="what time is it", source="voice")
    _pump(app)
    assert m.get("state") == "thinking" and not pc._fade.isActive()
    bus.publish("tool.start", name="apps", summary="Open app: Clock")
    _pump(app)
    assert (m.get("state"), m.get("progress")) == ("acting", "Open app: Clock")
    bus.publish("voice.state", state="speaking")
    bus.publish("assistant.reply", text="It's ten o'clock.", ok=True, via="fast", intent="time")
    _pump(app)
    assert m.get("state") == "speaking" and m.get("answer") == "It's ten o'clock."
    assert not pc._fade.isActive()                     # stays while speaking
    bus.publish("voice.state", state="follow_up")
    _pump(app)
    assert m.get("state") == "done" and m.get("statusText") == "Anything else?"
    assert pc._fade.isActive()                         # counting down now
    assert [x["role"] for x in m.get("messages")] == ["user", "assistant"]
    pc.fade_out()
    _pump(app, 600)
    assert not pc.view.isVisible()


def test_popup_false_trigger_and_nothing_heard(popup):
    pc, ctx, app = popup
    assert pc.create()
    ctx.bus.publish("voice.state", state="listening", kind="wake")
    ctx.bus.publish("voice.nothing_heard", kind="wake")
    ctx.bus.publish("voice.state", state="idle")
    _pump(app)
    assert pc.model.get("statusText") == "Didn't catch that" and pc._fade.interval() == 1800
    ctx.bus.publish("voice.state", state="listening", kind="wake")
    ctx.bus.publish("voice.state", state="transcribing")
    ctx.bus.publish("voice.false_trigger", text="travis")
    ctx.bus.publish("voice.state", state="idle")
    _pump(app)
    assert pc._fade.interval() == 200


def test_popup_question_click_and_chat(popup):
    from assistant.ui.popup import PendingConfirm

    pc, ctx, app = popup
    assert pc.create()
    req = ConfirmRequest(action=Action(tool="files", capability="files.delete", summary="Delete report.docx",
                                       details=[r"C:\Users\me\report.docx"]),
                         reasons=["changes need confirmation"], allow_task=True, allow_always=False)
    p = PendingConfirm(req, voice=True)
    pc.ask(p)
    _pump(app)
    m = pc.model
    assert m.get("confirmActive") and m.get("state") == "confirm"
    assert [o["id"] for o in m.get("confirmOptions")] == ["once", "task", "deny"]
    assert "report.docx" in m.get("confirmDetails") and m.get("confirmHint")
    pc.fade_out()
    _pump(app)
    assert m.get("visibleState")                       # never fades away with a question open
    m.choose("task")
    _pump(app)
    assert p.done.is_set() and p.choice is ConfirmChoice.TASK and not m.get("confirmActive")
    # mini chat
    pc.open_chat()
    _pump(app)
    assert m.get("expanded")
    m.submit("  open notepad ")
    m.collapse()
    _pump(app)
    assert pc.submitted_texts == ["open notepad"] and not m.get("expanded")


def test_popup_geometry_corners(popup):
    pc, ctx, app = popup
    screen = app.primaryScreen()
    a = screen.availableGeometry()
    for pos in ("top-left", "top-center", "top-right", "bottom-left", "bottom-center", "bottom-right"):
        ctx.config.set("appearance.popup_position", pos)
        g = pc.geometry_for(400, 100, screen)
        assert a.contains(g), pos
        assert (g.top() - a.top() < 40) == pos.startswith("top")
    ctx.config.set("appearance.popup_position", "top-center")
    g = pc.geometry_for(400, 100, screen)
    assert abs(g.center().x() - a.center().x()) <= 1


# ---------------------------------------------------------------------- click or voice, whichever first
class _FakePopup:
    def __init__(self, click: str | None = None, delay: float = 0.05):
        self.view = object()
        self.click, self.delay = click, delay
        self.asked: list = []
        self.closed: list = []

    def ask(self, p):
        self.asked.append(p)
        if self.click:
            threading.Timer(self.delay, lambda: p.resolve(ConfirmChoice(self.click))).start()

    def close_question(self, p):
        self.closed.append(p)


class _FakeVoice:
    def __init__(self, answer, active=True, delay=0.05):
        self.answer, self.active, self.delay = answer, active, delay
        self.stopped = threading.Event()

    def can_confirm_by_voice(self):
        return self.active

    def confirm_by_voice(self, request, stop):
        if stop.wait(self.delay):
            self.stopped.set()
            return None
        return self.answer


def _req():
    return ConfirmRequest(action=Action(tool="t", capability="files.delete", summary="Delete x"),
                          reasons=[], allow_task=True, allow_always=True)


def test_popup_confirmer_voice_or_click():
    from assistant.core.cancel import CancelToken
    from assistant.ui.confirm import PopupConfirmer

    fallback = object()
    # spoken "yes"
    pop = _FakePopup()
    c = PopupConfirmer(pop, fallback, _FakeVoice(ConfirmChoice.ONCE))  # type: ignore[arg-type]
    assert c.confirm(_req(), CancelToken()) is ConfirmChoice.ONCE
    assert pop.asked[0].voice and pop.closed == pop.asked
    # click first: the voice side is told to stop listening
    pop = _FakePopup(click="deny", delay=0.01)
    voice = _FakeVoice(ConfirmChoice.ONCE, delay=2.0)
    c = PopupConfirmer(pop, fallback, voice)  # type: ignore[arg-type]
    assert c.confirm(_req(), CancelToken()) is ConfirmChoice.DENY
    assert voice.stopped.wait(1.0)
    # typed request (voice not active): click only, no voice hint
    pop = _FakePopup(click="always")
    c = PopupConfirmer(pop, fallback, _FakeVoice(ConfirmChoice.ONCE, active=False))  # type: ignore[arg-type]
    assert c.confirm(_req(), CancelToken()) is ConfirmChoice.ALWAYS and not pop.asked[0].voice
    # cancelled task -> deny
    tok = CancelToken()
    tok.cancel("panic")
    c = PopupConfirmer(_FakePopup(), fallback, None)  # type: ignore[arg-type]
    assert c.confirm(_req(), tok) is ConfirmChoice.DENY


def test_player_without_output_device_does_not_hang(monkeypatch):
    from assistant.audio.player import Player

    p = Player()

    def no_device() -> None:
        raise OSError("no output device")

    monkeypatch.setattr(p, "_ensure_stream", no_device)
    with pytest.raises(OSError):
        p.play(np.ones(100, np.float32))
    assert not p.busy and p.wait(0.1)
