"""Milestone 5: the wizard window's controller logic (voice answers, retries, pages) without showing QML."""

import pytest

from assistant.onboarding.env import WizardEnv
from assistant.ui.onboarding import SetupWindow, pretty_hotkey


class FakeVoice:
    def __init__(self):
        self.calls = []
        self.av = {"speak": True, "listen": True, "wake": True, "lock": True}

    def available(self):
        return dict(self.av)

    def ask(self, text, *, speak, listen, voice=None):
        self.calls.append(("ask", text, speak, listen))

    def say(self, text, voice=None):
        self.calls.append(("say", text, voice))

    def cancel(self):
        self.calls.append(("cancel",))

    def preload(self):
        pass

    def record_name(self, name, takes=3, intro=""):
        self.calls.append(("record", name, intro))

    def enroll(self, lang):
        self.calls.append(("enroll", lang))

    def start_monitor(self, device=None):
        self.calls.append(("monitor", device))

    def stop_monitor(self):
        pass

    def close(self):
        self.calls.append(("close",))

    def asks(self):
        return [c for c in self.calls if c[0] == "ask"]


class DummyWindow:
    def setProperty(self, *a):  # noqa: N802
        pass

    def close(self):
        pass

    def deleteLater(self):  # noqa: N802
        pass


@pytest.fixture
def sw(ts):
    from PySide6.QtGui import QGuiApplication

    QGuiApplication.instance() or QGuiApplication([])
    system, *_ = ts
    env = WizardEnv(ctx=system.ctx, policy=system.policy, routines=system.services["routines"],
                    app_names=lambda: [a.name for a in system.services["apps"].apps()], first_run=True,
                    user_name_guess=lambda: "Lennox", language_guess=lambda: "en")
    voice = FakeVoice()
    w = SetupWindow(system.ctx, env, None, voice=voice)
    w.window = DummyWindow()          # pretend it is shown (no QML in unit tests)
    return w, voice, system.ctx


def test_voice_answer_moves_on_and_acknowledges(sw):
    w, voice, ctx = sw
    w._ask()
    assert voice.asks()[-1][3] is True                      # listening after the question
    w._on_voice("heard", {"text": "German please"})
    assert ctx.config.data.general.language == "de"
    assert w.wizard.current.id == "assistant_name"
    text = voice.asks()[-1][1]
    assert text.startswith("Alles klar.") and "heißen" in text   # next question already in German
    assert w.model.get("ui")["next"] == "Weiter"


def test_unclear_answers_retry_then_stop_listening(sw):
    w, voice, ctx = sw
    w.wizard.skip()
    w.wizard.skip()
    w.wizard.skip()
    w.wizard.skip()
    w.wizard.skip()
    w.wizard.skip()                                          # ... to "voice" (choice)
    while w.wizard.current.id != "personality":
        w.wizard.skip()
    w._refresh()
    for i in range(3):
        w._on_voice("heard", {"text": "banana bread"})
        assert w.model.get("statusKind") == "error"
        assert voice.asks()[-1][3] is (i < w.MAX_UNCLEAR)  # gives up listening after two tries
    assert w.wizard.current.id == "personality"
    w._on_voice("heard", {"text": "say that again"})
    assert "personality" in voice.asks()[-1][1].lower()


def test_wake_page_records_and_applies_calibration(sw):
    w, voice, ctx = sw
    w.wizard.skip()
    w.wizard.answer("Nova")
    w._refresh()
    assert w.wizard.current.id == "wake_test"
    w._ask()
    assert voice.calls[-1][0] == "record" and voice.calls[-1][1] == "Nova"
    w._on_voice("calibrated", {"result": {"sensitivity": 0.7, "variants": ["Nowa"], "detected": 3, "total": 3,
                                          "ok": True, "heard": [], "quiet": 0}})
    assert ctx.config.data.voice.wake_sensitivity == 0.7 and ctx.config.data.voice.wake_variants == ["Nowa"]
    st = w.model.get("pageState")
    assert st["step"] == "done" and st["detected"] == 3
    w._on_voice("heard", {"text": "next"})
    assert w.wizard.current.id == "user_name"


def test_voice_lock_page(sw):
    w, voice, ctx = sw
    while w.wizard.current.id != "voice_lock":
        w.wizard.skip()
    w._refresh()
    w._on_voice("heard", {"text": "yes"})
    assert voice.calls[-1] == ("enroll", "en")
    w._on_voice("enrolled", {})
    assert ctx.config.data.voice.voice_lock is True


def test_typed_values_are_coerced_and_validated(sw):
    w, voice, ctx = sw
    while w.wizard.current.id != "retention":
        w.wizard.skip()
    w._submit(30.0)                                          # QML numbers arrive as floats
    assert ctx.config.data.privacy.retention_days == 30
    assert isinstance(ctx.config.data.privacy.retention_days, int)
    w.wizard.back()
    w.wizard.back()
    while w.wizard.current.id != "hotkeys":
        w.wizard.back()
    w._submit({"push_to_talk": "ctrl+alt+x", "panic": "ctrl+alt+x", "mode": "press"})
    assert w.model.get("statusKind") == "error" and w.wizard.current.id == "hotkeys"


def test_finish(sw):
    w, voice, ctx = sw
    done = []
    w.completed.connect(lambda: done.append(True))
    w.finish()
    assert ctx.config.data.general.onboarding_done and done == [True] and ("close",) in voice.calls


def test_pretty_hotkey():
    assert pretty_hotkey("ctrl+alt+escape") == "Ctrl + Alt + Escape"
    assert pretty_hotkey("") == "-"
