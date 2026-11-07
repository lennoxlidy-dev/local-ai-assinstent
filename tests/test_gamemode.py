"""Milestone 7: automatic game mode (fake foreground window, fake clock)."""

import pytest

from assistant.core.gamemode import ForegroundInfo, GameModeWatcher, classify


def game(exe="C:\\Program Files (x86)\\Steam\\steamapps\\common\\Elden Ring\\eldenring.exe", pid=42, **kw):
    return ForegroundInfo(True, exe=exe, pid=pid, **kw)


@pytest.mark.parametrize("info,games,not_games,expected", [
    (None, [], [], False),
    (ForegroundInfo(False, exe="C:\\x\\game.exe"), [], [], False),
    (game(), [], [], True),                                                         # game library folder
    (ForegroundInfo(True, exe="C:\\Program Files\\Google\\Chrome\\chrome.exe", uses_3d=True), [], [], False),
    (ForegroundInfo(True, exe="C:\\Tools\\vlc.exe", exclusive=True), [], [], False),    # video player
    (ForegroundInfo(True, exe="D:\\Indie\\cool.exe", exclusive=True), [], [], True),    # exclusive fullscreen
    (ForegroundInfo(True, exe="D:\\Indie\\cool.exe", uses_3d=True), [], [], True),      # 3D API + fullscreen
    (ForegroundInfo(True, exe="D:\\Indie\\cool.exe", uses_3d=None), [], [], False),     # can't tell -> no
    (ForegroundInfo(True, exe="D:\\Indie\\cool.exe"), ["Cool.exe"], [], True),          # user list
    (game(), [], ["eldenring.exe"], False),                                             # user says no
])
def test_classify(info, games, not_games, expected):
    assert classify(info, games, not_games) is expected


class Clock:
    def __init__(self):
        self.t = 1000.0

    def __call__(self):
        return self.t


def make(ts):
    system, *_ = ts
    current: dict = {"info": None}
    clock = Clock()
    w = GameModeWatcher(system.ctx, probe=lambda: current["info"], clock=clock)
    return w, system.ctx, current, clock


def poll(w, clock, n=1, step=3.0):
    for _ in range(n):
        clock.t += step
        w.check()


def test_enters_after_a_few_seconds_and_leaves_after_the_grace_period(ts):
    w, ctx, cur, clock = make(ts)
    cur["info"] = game()
    poll(w, clock)
    assert not ctx.state.game_mode                       # one glimpse isn't enough
    poll(w, clock)
    assert ctx.state.game_mode and ctx.state.game_reason == "eldenring.exe"
    cur["info"] = ForegroundInfo(False)                  # alt-tab
    poll(w, clock, 3)
    assert ctx.state.game_mode                           # still within the grace period
    poll(w, clock, 5)
    assert not ctx.state.game_mode and ctx.state.game_reason == ""


def test_manual_switches_win(ts):
    w, ctx, cur, clock = make(ts)
    ctx.state.update(game_mode=True)                     # user: on, no game running
    poll(w, clock, 10)
    assert ctx.state.game_mode and ctx.state.game_reason == "manual"
    ctx.state.update(game_mode=False)
    cur["info"] = game(pid=7)
    poll(w, clock, 2)
    assert ctx.state.game_mode                           # automatic again for a game
    ctx.state.update(game_mode=False)                    # user: not for this game
    poll(w, clock, 5)
    assert not ctx.state.game_mode
    cur["info"] = game(exe="D:\\Games\\other\\other.exe", pid=8)
    poll(w, clock, 2)
    assert ctx.state.game_mode and ctx.state.game_reason == "other.exe"   # a different game: automatic


def test_exclusive_fullscreen_hides_the_popup(ts):
    from PySide6.QtGui import QGuiApplication

    from assistant.ui.popup import PopupController

    QGuiApplication.instance() or QGuiApplication([])
    w, ctx, cur, clock = make(ts)
    cur["info"] = ForegroundInfo(True, exclusive=True, exe="D:\\x\\fps.exe", pid=3)
    poll(w, clock, 2)
    assert ctx.state.game_mode and ctx.state.game_exclusive
    pc = PopupController(ctx)
    assert pc.create()
    ctx.bus.publish("voice.state", state="listening", kind="wake")
    from PySide6.QtCore import QCoreApplication

    QCoreApplication.processEvents()
    assert not pc.view.isVisible()                       # voice answers only
    pc.close()
