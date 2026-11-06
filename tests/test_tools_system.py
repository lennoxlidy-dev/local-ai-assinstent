"""Volume, brightness, clipboard, power, timers, input/screen, memory, routines."""

import datetime as dt
import time

from assistant.core.cancel import CancelToken
from assistant.permissions.gate import ConfirmChoice, Task
from assistant.permissions.policy import Risk
from assistant.tools.platform import UiElement
from assistant.tools.screen import parse_keys
from assistant.tools.system import parse_duration, parse_time_of_day


def run(ts, name, task=None, /, **args):
    return ts[0].invoke(name, args, task or Task(source="test"))


def test_volume_and_brightness(ts):
    system, p, conf, home = ts
    assert run(ts, "volume", level=40).text == "Volume 40%."
    assert run(ts, "volume", change=-15).text == "Volume 25%."
    assert run(ts, "volume", change=90).text == "Volume 100%."            # clamped
    assert not run(ts, "volume", change=200).ok
    assert run(ts, "volume", mute=True).text == "Volume 100% (muted)."
    assert run(ts, "volume", level="30").ok                      # string from a sloppy model is fine
    assert not run(ts, "volume", level=130).ok
    assert run(ts, "brightness", change=-20).text == "Brightness 50%."
    assert conf.requests == []


def test_clipboard_is_untrusted_data(ts):
    system, p, conf, home = ts
    assert run(ts, "clipboard", action="set", text="hello").ok and p.clipboard.text == "hello"
    r = run(ts, "clipboard", action="get")
    assert r.untrusted and "hello" in r.text


def test_power(ts):
    system, p, conf, home = ts
    assert run(ts, "power", action="lock").ok and conf.requests == []
    conf.choice = ConfirmChoice.DENY
    assert not run(ts, "power", action="shutdown").ok
    assert p.power.calls == ["lock"] and conf.requests[-1].action.risks == {Risk.POWER}


def test_duration_and_time_parsing():
    assert parse_duration("5 min") == 300
    assert parse_duration("1h 30m") == 5400
    assert parse_duration("2 Stunden") == 7200
    assert parse_duration("90 Sekunden") == 90
    assert parse_duration("soon") is None
    now = dt.datetime(2026, 11, 6, 16, 0)
    assert parse_time_of_day("17:30", now) == dt.datetime(2026, 11, 6, 17, 30)
    assert parse_time_of_day("5 pm", now) == dt.datetime(2026, 11, 6, 17, 0)
    assert parse_time_of_day("9:15", now) == dt.datetime(2026, 11, 7, 9, 15)       # tomorrow
    assert parse_time_of_day("18 Uhr", now) == dt.datetime(2026, 11, 6, 18, 0)
    assert parse_time_of_day("25:00", now) is None


def test_timers_fire_and_notify(ts):
    system, p, conf, home = ts
    seen = []
    system.ctx.bus.subscribe("ui.notify", lambda e: seen.append(e.data))
    r = run(ts, "timer", action="set_timer", duration="10 min", label="pasta")
    assert r.ok and "10 min" in r.text
    assert run(ts, "timer", action="set_reminder", at="23:59", label="call mom").ok
    assert len(system.timers.pending()) == 2
    fired = system.timers.fire_due(time.time() + 11 * 60)
    assert [t.label for t in fired] == ["pasta"] and seen[0]["message"] == "pasta"
    assert run(ts, "timer", action="cancel", label="mom").ok
    assert system.timers.pending() == []
    assert not run(ts, "timer", action="set_timer").ok


def test_input_needs_confirmation_once_per_task(ts):
    system, p, conf, home = ts
    conf.choice = ConfirmChoice.TASK
    task = Task()
    assert run(ts, "mouse", task, action="click", x=100, y=200).ok
    assert run(ts, "keyboard", task, action="type", text="Hallo Welt ü€😀").ok
    assert run(ts, "keyboard", task, action="hotkey", keys="ctrl+s").ok
    assert len(conf.requests) == 1                               # granted for the rest of the task
    assert ("hotkey", ("ctrl", "s")) in p.input.events
    assert not run(ts, "mouse", task, action="click", x=5000, y=5000).ok   # off-screen
    assert not run(ts, "keyboard", task, action="hotkey", keys="ctrl+bogus").ok


def test_parse_keys():
    assert parse_keys("Strg+Shift+Entf") == ["ctrl", "shift", "delete"]
    assert parse_keys("win + r") == ["win", "r"]
    assert parse_keys(["alt", "F4"]) == ["alt", "f4"]


def test_click_element_uia_then_ocr(ts):
    system, p, conf, home = ts
    conf.choice = ConfirmChoice.TASK
    p.uia.by_hwnd[None] = [UiElement("Save", "ButtonControl", 10, 10, 50, 20),
                           UiElement("Save as…", "MenuItemControl", 10, 40, 80, 20)]
    task = Task()
    assert "Clicked “Save”" in run(ts, "click_element", task, name="save").text
    assert p.uia.activated == ["Save"]
    r = run(ts, "click_element", task, name="Save as PDF")       # not in UIA -> OCR fallback clicks the text
    assert r.ok and "OCR" in r.text and p.input.events[-1] == ("click", 445, 307, "left", 1)
    assert not run(ts, "click_element", task, name="Nonexistent button").ok


def test_read_screen_is_untrusted(ts):
    r = run(ts, "read_screen")
    assert r.ok and r.untrusted and "Save as PDF" in r.text


def test_memory(ts):
    system, p, conf, home = ts
    assert run(ts, "remember", fact="My sister Anna's birthday is May 3rd").ok
    assert run(ts, "remember", fact="I prefer dark roast coffee").ok
    assert "Anna" in run(ts, "recall", query="birthday of my sister").text
    assert run(ts, "forget", query="coffee").ok
    assert [f.text for f in system.services["memory"].all()] == ["My sister Anna's birthday is May 3rd"]


def test_routines_validate_and_run_each_step_through_gate(ts):
    system, p, conf, home = ts
    steps = [{"tool": "open_app", "args": {"name": "Steam"}}, {"tool": "open_app", "args": {"name": "Discord"}},
             {"tool": "volume", "args": {"level": 40}}, {"tool": "wait", "args": {"seconds": 0.01}},
             {"tool": "say", "args": {"text": "Have fun!"}}]
    assert run(ts, "create_routine", name="Gaming mode", steps=steps).ok
    bad = run(ts, "create_routine", name="x", steps=[{"tool": "volume", "args": {"level": 400}}])
    assert not bad.ok and "Step 1" in bad.text
    nested = run(ts, "create_routine", name="y", steps=[{"tool": "run_routine", "args": {"name": "x"}}])
    assert not nested.ok
    r = run(ts, "run_routine", name="gaming MODE")
    assert r.ok, r.text
    assert p.apps.launched == ["Steam", "Discord"] and p.audio.volume == 40 and "Have fun!" in r.text
    tools_logged = [e.tool for e in reversed(system.action_log.recent(10))]
    assert tools_logged[-4:] == ["run_routine", "open_app", "open_app", "volume"]


def test_routine_step_permissions_still_apply(ts):
    system, p, conf, home = ts
    (home / "old.txt").write_text("x")
    assert run(ts, "create_routine", name="cleanup",
               steps=[{"tool": "delete_path", "args": {"path": str(home / "old.txt")}}]).ok
    conf.choice = ConfirmChoice.DENY
    r = run(ts, "run_routine", name="cleanup")
    assert not r.ok and (home / "old.txt").exists()


def test_cancel_stops_typing(ts):
    system, p, conf, home = ts
    conf.choice = ConfirmChoice.ONCE
    task = Task(cancel=CancelToken())
    task.cancel.cancel("panic")
    assert run(ts, "keyboard", task, action="type", text="hello").text == "Cancelled."
    assert not any(e[0] == "type" for e in p.input.events)


def test_keyboard_focuses_target_window_first(ts):
    system, p, conf, home = ts
    console = p.windows.add("Windows PowerShell", "WindowsTerminal.exe")
    pad = p.windows.add("Untitled - Notepad", "Notepad.exe")
    p.windows.fg = console.hwnd
    assert run(ts, "keyboard", action="type", text="hi", window="notepad").ok
    assert p.windows.fg == pad.hwnd and p.input.events[-1] == ("type", "hi")
    assert "into: Untitled - Notepad" in conf.requests[-1].action.details
