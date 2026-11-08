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
    p.sound.play = lambda path, wait, cancelled=None, **kw: order.append(("sound", path))
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


# ---------------------------------------------------------------------- the playing logic (fake MCI, fake clock)
class _Clock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


class _Ticks:
    """Stands in for the stop event: wait() moves the fake clock instead of sleeping."""

    def __init__(self, clock, stop_at=None):
        self.clock, self.stop_at = clock, stop_at

    def wait(self, dt):
        self.clock.t = round(self.clock.t + dt, 3)
        return self.stop_at is not None and self.clock.t >= self.stop_at


class _Mci:
    """A sound of ``length`` seconds: 'status … mode' says playing until it has run that long."""

    def __init__(self, length, clock, broken=False):
        self.length, self.clock, self.broken = length, clock, broken
        self.plays, self.volumes, self.closed, self.started = 0, [], False, 0.0

    def __call__(self, cmd):
        if cmd.startswith("play "):
            self.plays += 1
            self.started = self.clock()
        elif cmd.startswith("status "):
            return "stopped" if self.broken or self.clock() - self.started >= self.length else "playing"
        elif cmd.startswith("setaudio "):
            self.volumes.append(int(cmd.rsplit(" ", 1)[1]))
        elif cmd.startswith("close "):
            self.closed = True
        return ""


def _session(length, *, until_at=None, seconds=0.0, stop_at=None, broken=False):
    from assistant.audio.mci import play_session

    clock = _Clock()
    mci = _Mci(length, clock, broken)
    until = (lambda: clock.t >= until_at) if until_at is not None else None
    play_session(mci, "s1", "C:\\x.wav", stop=_Ticks(clock, stop_at), until=until, seconds=seconds, clock=clock)
    return mci, clock.t


def test_a_sound_plays_once():
    mci, end = _session(3.0)
    assert mci.plays == 1 and 3.0 <= end < 3.3 and mci.closed and mci.volumes == []


def test_a_set_time_repeats_a_short_sound():
    mci, end = _session(3.0, seconds=7.0)
    assert mci.plays == 3 and 7.0 <= end < 7.3          # 2 1/3 rounds: exactly 7 s, the end faded out
    assert mci.volumes == [900, 800, 700, 600, 500, 400, 300, 200, 100, 0]


def test_a_set_time_cuts_a_long_sound():
    """The report: "Highway to Hell" (208 s) with 60 s set played the whole song - it must stop at 60 s."""
    mci, end = _session(208.4, seconds=60.0)
    assert mci.plays == 1 and 60.0 <= end < 60.3 and mci.closed
    assert mci.volumes == [900, 800, 700, 600, 500, 400, 300, 200, 100, 0]       # faded out over the last second


def test_until_the_routine_is_done_repeats_then_fades_out():
    mci, end = _session(3.0, until_at=8.0)
    assert mci.plays == 3 and 9.0 <= end < 9.3          # done at 8 s, then a 1 s fade
    assert mci.volumes == [900, 800, 700, 600, 500, 400, 300, 200, 100, 0]


def test_until_done_still_keeps_the_minimum_time():
    mci, end = _session(10.0, until_at=2.0, seconds=5.0)
    assert mci.plays == 1 and 5.0 <= end < 5.3 and mci.volumes[-1] == 0      # done at 2 s - it ends at 5 s


def test_stop_ends_a_sound_at_once():
    mci, end = _session(30.0, until_at=99.0, stop_at=1.0)
    assert end == 1.0 and mci.closed and mci.volumes == []


def test_a_sound_that_never_plays_is_not_repeated():
    mci, end = _session(3.0, seconds=30.0, broken=True)
    assert mci.plays == 1 and end < 2.0 and mci.closed


# ---------------------------------------------------------------------- the step's options in a routine
def test_until_done_and_the_time_reach_the_player(ts):
    system, p, _conf, home = ts
    intro = _music(home, "intro.wav")
    system.services["routines"].save(Routine("Gaming mode", [
        {"tool": "play_sound", "args": {"path": str(intro), "until_done": True, "seconds": 8}},
        {"tool": "open_app", "args": {"name": "Steam"}}]))
    a = Assistant(system.ctx, system, None)
    assert a.handle("gaming mode").ok
    opts = p.sound.options[-1]
    assert opts["seconds"] == 8.0 and opts["until"]() is True     # the routine is done -> may end now
    assert not system.invoke("play_sound", {"path": str(intro), "wait": True, "until_done": True}, Task()).ok
    r = system.invoke("play_sound", {"path": str(intro), "seconds": 60}, Task())
    assert r.ok and r.text == "Playing intro.wav for 60 s." and p.sound.options[-1]["until"] is None


def test_a_routine_from_before_plays_its_minimum_as_the_time(ts):
    """Up to 0.1.9 the time was stored as "min_seconds" (meant as "play 60 s of the song"): read the same way."""
    system, p, _conf, home = ts
    intro = _music(home, "song.mp3")
    system.services["routines"].save(Routine("Gaming mode", [
        {"tool": "play_sound", "args": {"path": str(intro), "min_seconds": 60}}]))
    assert Assistant(system.ctx, system, None).handle("gaming mode").ok
    assert p.sound.options[-1]["seconds"] == 60.0 and p.sound.options[-1]["until"] is None


def test_a_routine_can_check_drivers_and_say_the_result(ts):
    import json

    from assistant.tools.platform import CommandOutput

    system, p, *_ = ts
    upd = {"id": "11111111-2222-3333-4444-555555555555", "title": "Intel Corporation - Display - 31.0.101.5186",
           "size": 1, "date": "2026-09-30"}
    p.shell.run = lambda *a: CommandOutput(0, json.dumps([upd]), "")
    system.services["routines"].save(Routine("Maintenance", [
        {"tool": "say", "args": {"text": "Checking your drivers"}},
        {"tool": "driver_updates", "args": {"action": "check"}}]))
    spoken = []
    r = Assistant(system.ctx, system, None).handle("start maintenance", source="voice", on_content=spoken.append)
    assert r.ok and r.text == ("Checking your drivers. 1 driver update available: Intel Corporation - Display - "
                               "31.0.101.5186. Say “update my drivers” to install them.")
    assert [x.strip() for x in spoken] == ["Checking your drivers.", r.text[len("Checking your drivers. "):]]
