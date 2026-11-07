"""Routines that sound like something: "say" spoken when reached, play_sound, and saying a routine's name."""

import pytest

from assistant.core.assistant import Assistant
from assistant.intents.fastpath import match_routine
from assistant.permissions.gate import Task
from assistant.permissions.policy import Access
from assistant.storage.routines import Routine


def _music(home, name="intro.mp3"):
    f = home / "Music" / name
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_bytes(b"ID3 fake mp3")
    return f


def test_play_sound_step(ts):
    system, p, _conf, home = ts
    intro = _music(home)
    r = system.invoke("play_sound", {"path": str(intro)}, Task(source="routine"))
    assert r.ok and p.sound.played == [(str(intro), False)]
    assert system.invoke("play_sound", {"path": str(intro), "wait": True}, Task()).ok
    assert p.sound.played[-1] == (str(intro), True)
    bad = home / "Music" / "notes.txt"
    bad.write_text("x")
    assert not system.invoke("play_sound", {"path": str(bad)}, Task()).ok              # not a sound file
    assert not system.invoke("play_sound", {"path": str(home / "Music" / "gone.mp3")}, Task()).ok
    secret = home / "Secret" / "x.mp3"
    secret.parent.mkdir()
    secret.write_bytes(b"x")
    system.policy.set_folder(str(secret.parent), Access.NONE)
    system.reload_policy()
    assert not system.invoke("play_sound", {"path": str(secret)}, Task()).ok          # folder rules apply
    assert len(p.sound.played) == 2
    assert system.registry.get("play_sound").llm is False       # routines only - not in the model's tool list


@pytest.mark.parametrize("said", ["gaming mode", "start gaming mode", "activate my gaming mode now", "gaming mode on",
                                  "run the gaming mode", "gaming mod", "spielmodus an", "starte den spielmodus"])
def test_routine_names_are_recognized(said):
    assert match_routine(said, ["Gaming mode", "Spielmodus", "Good night"]) in ("Gaming mode", "Spielmodus")


@pytest.mark.parametrize("said", ["what is gaming mode", "open steam", "gaming", "good morning", "start the game",
                                  "play gaming music"])
def test_other_sentences_are_not_routines(said):
    assert match_routine(said, ["Gaming mode", "Good night"]) is None


def test_saying_the_name_runs_the_routine_and_speaks_as_it_goes(ts):
    system, p, _conf, home = ts
    intro = _music(home)
    order = []
    p.sound.play = lambda path, wait, cancelled=None: order.append(("sound", path))
    launch = p.apps.launch
    p.apps.launch = lambda app, *a, **k: (order.append(("open", app.name)), launch(app, *a, **k))[1]
    system.services["routines"].save(Routine("Gaming mode", [
        {"tool": "say", "args": {"text": "Gaming mode activated"}},
        {"tool": "play_sound", "args": {"path": str(intro)}},
        {"tool": "open_app", "args": {"name": "Steam"}},
        {"tool": "volume", "args": {"level": 40}},
        {"tool": "say", "args": {"text": "Have fun!"}}]))
    a = Assistant(system.ctx, system, None)                      # no language model needed
    spoken = []

    def speak(text):
        spoken.append(text)
        order.append(("say", text.strip()))

    r = a.handle("Jarvis, start gaming mode", source="voice", on_content=speak)
    assert (r.via, r.intent, r.ok) == ("fast", "routine.run", True)
    assert r.text == "Gaming mode activated. Have fun!"
    assert order == [("say", "Gaming mode activated."), ("sound", str(intro)), ("open", "Steam"), ("say", "Have fun!")]
    assert "Steam" in p.apps.launched and p.audio.volume == 40
    typed = a.handle("gaming mode")                              # typed: the same lines as the answer
    assert typed.ok and typed.text == "Gaming mode activated. Have fun!"


def test_a_routine_without_announcement_says_done(ts):
    system, p, *_ = ts
    system.services["routines"].save(Routine("Work mode", [{"tool": "volume", "args": {"level": 30}}]))
    r = Assistant(system.ctx, system, None).handle("work mode")
    assert r.ok and r.text == "Work mode: done." and p.audio.volume == 30


def test_stop_also_stops_a_routine_sound(ts):
    system, p, *_ = ts
    a = Assistant(system.ctx, system, None)
    a.handle("stop")
    assert p.sound.stopped == 1
