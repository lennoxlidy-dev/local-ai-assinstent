"""Media tools: control any player (Windows media session / SMTC) and "play <song>" in the music app."""

from __future__ import annotations

import logging
import re
import time
import urllib.parse
from pathlib import Path
from typing import Any

from .apps import DesktopGuard, _svc, open_app, window_matches
from .base import Plan, Tool, ToolContext, ToolError, ToolRegistry, ToolResult
from .platform import UiElement
from .schema import Bool, Int, Str, obj

log = logging.getLogger(__name__)

ACTION_WORDS = {"play": "Playing", "pause": "Paused", "toggle": "Toggled playback", "next": "Next track",
                "previous": "Previous track", "stop": "Stopped"}

# Spotify's "play" button for a search result: "Play <title> by <artist>" (EN), "<title> abspielen" (DE)...
_PLAY_RESULT = [re.compile(p, re.I) for p in (r"^play\s+\S.+", r"^.+\s+(abspielen|wiedergeben)$",
                                              r"^(abspielen|wiedergeben)\s+\S.+")]


def _plan_media(a: dict[str, Any], ctx: ToolContext) -> Plan:
    return Plan(f"Media: {a['action']}", capability="media")


def _run_media(a: dict[str, Any], ctx: ToolContext, plan: Plan) -> ToolResult:
    action = a["action"]
    if action == "status":
        st = ctx.platform.media.status()
        if not st:
            return ToolResult(True, "Nothing is playing.")
        title, artist = st.get("title") or "", st.get("artist") or ""
        who = f"{title} by {artist}" if title and artist else (title or artist or "unknown track")
        state = (st.get("status") or "").capitalize()
        app = f" ({st['app']})" if st.get("app") else ""
        return ToolResult(True, f"{state + ': ' if state else ''}{who}{app}", data=st)
    ok = ctx.platform.media.command(action)
    if not ok:
        return ToolResult(False, "No media player responded.")
    return ToolResult(True, f"{ACTION_WORDS.get(action, action)}.")


SOUND_EXT = (".mp3", ".wav", ".wma", ".m4a", ".aac")


def _plan_sound(a: dict[str, Any], ctx: ToolContext) -> Plan:
    name = a["path"].replace("\\", "/").rsplit("/", 1)[-1]
    ext = ("." + name.rsplit(".", 1)[-1].lower()) if "." in name else ""
    if ext not in SOUND_EXT:
        raise ToolError(f"{name} is not a sound file - use an MP3 or WAV file.")
    if a.get("wait") and a.get("until_done"):
        raise ToolError("A sound can either play before the next step (wait) or until the routine is done - not both.")
    return Plan(f"Play the sound {name}", paths=[(a["path"], "read")], capability="media")


def _run_sound(a: dict[str, Any], ctx: ToolContext, plan: Plan) -> ToolResult:
    p = Path(ctx.path(a["path"]).path)
    if not p.is_file():
        raise ToolError(f"The sound file {p} doesn't exist (any more).")
    wait, until_done, min_s = bool(a.get("wait")), bool(a.get("until_done")), float(a.get("min_seconds") or 0)
    token, finished = ctx.task.cancel, ctx.task.finished
    ctx.platform.sound.play(str(p), wait, (lambda: token.cancelled) if wait else None,
                            until=finished.is_set if until_done else None, min_s=min_s)
    how = (" until the routine is done" if until_done else "") + (f" (at least {min_s:g} s)" if min_s else "")
    return ToolResult(True, (f"Played {p.name}." if wait else f"Playing {p.name}") + (how if not wait else "")
                      + ("" if wait else "."))


def pick_play_button(elements: list[UiElement]) -> UiElement | None:
    cands = [e for e in elements if e.enabled and "button" in e.control_type.lower()
             and any(p.match(e.name.strip()) for p in _PLAY_RESULT)]
    if not cands:
        return None
    cands.sort(key=lambda e: (e.y, e.x))  # top result card is highest on the page
    return cands[0]


def play_in_spotify(ctx: ToolContext, query: str) -> str:
    p = ctx.platform
    svc = _svc(ctx)
    app = svc.resolve("Spotify")
    rule = svc.desktop_rule(app)
    cancel = ctx.task.cancel
    with DesktopGuard(p):
        open_app(ctx, app, rule)
        p.apps.open_target("spotify:search:" + urllib.parse.quote(query))
        deadline = time.monotonic() + 8.0
        hwnd = None
        while time.monotonic() < deadline and not cancel.cancelled:
            wins = [w for w in p.windows.list_windows() if window_matches(w, app)]
            hwnd = wins[0].hwnd if wins else None
            if hwnd is not None:
                try:
                    btn = pick_play_button(p.uia.elements(hwnd, 600, cancel))
                except Exception as e:
                    log.debug("spotify UIA scan failed: %s", e)
                    btn = None
                if btn is not None:
                    how = p.uia.activate(btn, hwnd)
                    log.info("spotify: started %r via %s", btn.name, how)
                    return f"Playing {query} on Spotify."
            cancel.wait(0.4)
    return (f"I searched Spotify for “{query}” but couldn't press play automatically - "
            "the results are open in Spotify.")


def _plan_play(a: dict[str, Any], ctx: ToolContext) -> Plan:
    app = (a.get("app") or ctx.services["config_get"]("apps.music_app") or "Spotify").strip()
    return Plan(f"Play “{a['query']}” in {app}", capability="media", state={"app": app})


def _run_play(a: dict[str, Any], ctx: ToolContext, plan: Plan) -> ToolResult:
    app, query = plan.state["app"], a["query"].strip()
    low = app.casefold()
    if "spotify" in low:
        msg = play_in_spotify(ctx, query)
        return ToolResult("couldn't" not in msg, msg)
    if "youtube" in low:
        url = ("https://music.youtube.com/search?q=" if "music" in low else
               "https://www.youtube.com/results?search_query=") + urllib.parse.quote_plus(query)
        ctx.platform.apps.open_target(url)
        return ToolResult(True, f"Opened YouTube search for “{query}”.")
    svc = _svc(ctx)
    target = svc.resolve(app)
    open_app(ctx, target, svc.desktop_rule(target))
    if not ctx.task.cancel.wait(2.0):
        ctx.platform.media.command("play")
    return ToolResult(True, f"Opened {target.name}; I can't search inside it, so I pressed play.")


def register(reg: ToolRegistry) -> None:
    reg.register(Tool(
        "media", "Control whatever is playing (Spotify, browser, VLC...): play, pause, toggle, next, previous, "
                 "stop, status.",
        obj(("action",), action=Str("Command", enum=("play", "pause", "toggle", "next", "previous", "stop",
                                                      "status"))),
        "media", _plan_media, _run_media))
    reg.register(Tool(
        "play_music", "Play a song, artist, album or playlist in the user's music app.",
        obj(("query",), query=Str("What to play, e.g. 'Bohemian Rhapsody' or 'lofi beats'", min_len=1, max_len=200),
            app=Str("Music app; empty = the user's default", max_len=60)),
        "media", _plan_play, _run_play))
    reg.register(Tool(   # for routines ("gaming mode" starts with your sound); not offered to the language model
        "play_sound", "Play a sound file (MP3 or WAV) in the background, e.g. at the start of a routine.",
        obj(("path",), path=Str("The sound file", min_len=1, max_len=400),
            wait=Bool("Wait until the sound has finished before the next step"),
            until_done=Bool("Keep playing (repeating) until the routine is done, then fade out"),
            min_seconds=Int("Play at least this many seconds (a shorter sound repeats)", min=0, max=600)),
        "media", _plan_sound, _run_sound, llm=False))


__all__ = ["pick_play_button", "play_in_spotify", "register", "ToolError"]
