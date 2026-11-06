"""Apps, windows, virtual desktops, media/Spotify flow."""

from assistant.permissions.gate import ConfirmChoice, Task
from assistant.permissions.policy import Risk
from assistant.tools.apps import AppMatcher
from assistant.tools.media import pick_play_button
from assistant.tools.platform import AppEntry, UiElement


def run(ts, name, /, **args):
    return ts[0].invoke(name, args, Task(source="test"))


def test_matcher():
    apps = [AppEntry("Word", "w"), AppEntry("WordPad", "wp"), AppEntry("Google Chrome", "c"),
            AppEntry("Visual Studio Code", "v"), AppEntry("Spotify", "s"), AppEntry("Microsoft Teams", "t")]
    m = AppMatcher(apps, {"browser": "Google Chrome", "music": "Spotify", "vs code": "Visual Studio Code"})
    assert m.match("word")[0].name == "Word"
    assert m.match("the browser")[0].name == "Google Chrome"
    assert m.match("chrome")[0].name == "Google Chrome"
    assert m.match("Spotfy")[0].name == "Spotify"       # typo / STT error
    assert m.match("music")[0].name == "Spotify"
    assert m.match("teams")[0].name == "Microsoft Teams"
    assert m.match("vs code")[0].name == "Visual Studio Code"
    assert m.match("photoshop")[0] is None


def test_open_app_on_current_desktop(ts):
    system, p, conf, home = ts
    r = run(ts, "open_app", name="chrome")
    assert r.ok and p.apps.launched == ["Google Chrome"] and conf.requests == []
    r = run(ts, "open_app", name="chrome")                 # already running -> focus, no 2nd launch
    assert "Switched" in r.text and p.apps.launched == ["Google Chrome"]


def test_open_spotify_on_assistant_desktop_restores_user(ts):
    system, p, conf, home = ts
    word = p.windows.add("Thesis.docx - Word", "WINWORD.EXE")
    p.windows.fg = word.hwnd
    r = run(ts, "open_app", name="spotify")                 # default rule: spotify -> assistant desktop
    assert r.ok and "Assistant desktop" in r.text, r.text
    assert [d.name for d in p.desktops.list()] == ["", "Assistant"]
    sp = next(w for w in p.windows.windows if w.exe == "Spotify.exe")
    assert p.desktops.where[sp.hwnd] == 2                   # moved to the Assistant desktop
    assert p.windows.fg == word.hwnd and p.desktops.current == 1   # user's focus/desktop restored
    run(ts, "open_app", name="spotify")                     # second time: no new desktop, no new launch
    assert len(p.desktops.desks) == 2 and p.apps.launched == ["Spotify"]


def test_play_music_in_spotify(ts):
    system, p, conf, home = ts
    word = p.windows.add("Thesis.docx - Word", "WINWORD.EXE")
    p.windows.fg = word.hwnd

    class Uia:
        activated = []

        def elements(self, hwnd, limit, cancel):
            return [UiElement("Play", "ButtonControl", 900, 1000, 30, 30),
                    UiElement("Play Bohemian Rhapsody by Queen", "ButtonControl", 300, 250, 48, 48),
                    UiElement("Play Somebody to Love by Queen", "ButtonControl", 300, 400, 30, 30)]

        def activate(self, el, hwnd):
            self.activated.append(el.name)
            return "invoke"

    p.uia = Uia()
    r = run(ts, "play_music", query="Bohemian Rhapsody")
    assert r.ok, r.text
    assert p.uia.activated == ["Play Bohemian Rhapsody by Queen"]
    assert p.apps.opened[-1] == "spotify:search:Bohemian%20Rhapsody"
    assert p.desktops.current == 1 and p.windows.fg == word.hwnd   # Spotify's activation was undone


def test_pick_play_button_german():
    els = [UiElement("Wiedergeben", "ButtonControl", 900, 1000, 30, 30),
           UiElement("Bohemian Rhapsody von Queen abspielen", "ButtonControl", 300, 250, 48, 48)]
    assert pick_play_button(els).name.endswith("abspielen")


def test_window_close_detects_unsaved(ts):
    system, p, conf, home = ts
    clean = p.windows.add("Notes.txt - Notepad", "Notepad.exe")
    dirty = p.windows.add("*Thesis.docx - Word", "WINWORD.EXE")
    assert run(ts, "window", action="close", target="notepad").ok
    assert p.windows.closed == [clean.hwnd] and conf.requests == []   # apps.close is allowed by default
    conf.choice = ConfirmChoice.DENY
    r = run(ts, "window", action="close", target="word")
    assert not r.ok and conf.requests[-1].action.risks == {Risk.CLOSE_UNSAVED}
    assert dirty.hwnd not in p.windows.closed
    r = run(ts, "window", action="kill", target="word")
    assert not r.ok and conf.requests[-1].action.risks == {Risk.FORCE_CLOSE} and p.windows.killed == []


def test_window_list_and_focus(ts):
    system, p, conf, home = ts
    a = p.windows.add("Inbox - Outlook", "olk.exe")
    p.windows.add("YouTube - Google Chrome", "chrome.exe")
    assert "Inbox - Outlook" in run(ts, "window", action="list").text
    assert run(ts, "window", action="focus", target="outlook").ok and p.windows.fg == a.hwnd
    assert not run(ts, "window", action="focus", target="photoshop").ok


def test_desktop_tool(ts):
    system, p, conf, home = ts
    assert run(ts, "desktop", action="create", target="Work").ok
    assert run(ts, "desktop", action="switch", target="next").ok and p.desktops.current == 2
    assert run(ts, "desktop", action="switch", target="eins").ok and p.desktops.current == 1
    assert not run(ts, "desktop", action="switch", target="9").ok
    assert run(ts, "desktop", action="switch", target="assistant").ok
    assert p.desktops.list()[-1].name == "Assistant"


def test_media(ts):
    system, p, conf, home = ts
    assert run(ts, "media", action="next").ok and p.media.commands == ["next"]
    assert "Bohemian Rhapsody by Queen" in run(ts, "media", action="status").text


def test_open_url_only_http(ts):
    system, p, conf, home = ts
    assert run(ts, "open_url", url="example.com").ok and p.apps.opened[-1] == "https://example.com"
    assert not run(ts, "open_url", url="file:///C:/Windows/System32/calc.exe").ok
    assert not run(ts, "open_url", url="javascript:alert(1)").ok


def test_matcher_knows_german_and_english_windows_names():
    m = AppMatcher([AppEntry("Notepad", "n"), AppEntry("Rechner", "r"), AppEntry("Einstellungen", "s")])
    assert m.match("Editor")[0].name == "Notepad"
    assert m.match("calculator")[0].name == "Rechner"
    assert m.match("settings")[0].name == "Einstellungen"
