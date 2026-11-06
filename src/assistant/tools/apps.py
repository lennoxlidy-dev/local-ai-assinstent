"""Apps, windows and virtual desktops.

* ``AppMatcher``: fuzzy matching of spoken names against the installed-app
  index (Start Menu + Store apps) plus user aliases ("music" -> "Spotify").
* ``open_app`` honours per-app desktop rules: e.g. Spotify opens on the
  dedicated "Assistant" desktop and the user's current desktop/focus is
  restored afterwards (``DesktopGuard``).
"""

from __future__ import annotations

import logging
import re
import time
import unicodedata
from collections.abc import Callable
from typing import Any

from ..core.cancel import CancelToken
from ..permissions.policy import Risk
from .base import Plan, Tool, ToolContext, ToolError, ToolRegistry, ToolResult
from .platform import AppEntry, Platform, WindowInfo
from .schema import Bool, Str, obj

log = logging.getLogger(__name__)

UNSAVED_MARKERS = re.compile(r"(^\*|\s\*\s|\*\s*[-–—|]|\*$|●|•|\(modified\)|\[modified\]|– edited|- edited|"
                             r"\(geändert\)|\(bearbeitet\))", re.I)


def norm_name(s: str) -> str:
    s = unicodedata.normalize("NFKC", s).casefold()
    s = re.sub(r"[®™©]", "", s)
    s = re.sub(r"\s+", " ", s)
    return s.strip()


class AppMatcher:
    def __init__(self, apps: list[AppEntry], aliases: dict[str, str] | None = None):
        self.apps = [a for a in apps if a.name.strip()]
        self.aliases = {norm_name(k): v for k, v in (aliases or {}).items()}
        self._names = [norm_name(a.name) for a in self.apps]

    def match(self, query: str) -> tuple[AppEntry | None, list[AppEntry]]:
        """Best app for ``query`` (or None) and close alternatives."""
        from rapidfuzz import fuzz, process

        q = norm_name(query)
        for prefix in ("the ", "die ", "der ", "das ", "app ", "programm "):
            if q.startswith(prefix):
                q = q[len(prefix):]
        q = self.aliases.get(q, q) if q in self.aliases else q
        q = norm_name(q)
        if not q or not self.apps:
            return None, []
        exact = [a for a, n in zip(self.apps, self._names, strict=True) if n == q]
        if exact:
            return min(exact, key=lambda a: a.kind != "desktop"), exact[1:]
        starts = [a for a, n in zip(self.apps, self._names, strict=True) if n.startswith(q + " ") or n == q]
        if len(starts) == 1:
            return starts[0], []
        scored = process.extract(q, self._names, scorer=fuzz.WRatio, limit=5)
        if not scored:
            return None, []
        best_name, best_score, best_idx = scored[0]
        alts = [self.apps[i] for _, s, i in scored[1:] if s >= 70]
        # whole-word containment beats fuzzy noise: "word" -> "Microsoft Word", not "WordPad"
        words = [a for a, n in zip(self.apps, self._names, strict=True) if q in n.split()]
        if words and best_score < 95:
            words.sort(key=lambda a: len(a.name))
            return words[0], [w for w in words[1:4]]
        if best_score >= 85 or (best_score >= 75 and (len(scored) == 1 or best_score - scored[1][1] >= 8)):
            return self.apps[best_idx], alts
        return None, [self.apps[i] for _, s, i in scored if s >= 60][:4]


def window_matches(win: WindowInfo, app: AppEntry) -> bool:
    if app.exe and win.exe and win.exe.casefold() == app.exe.casefold():
        return True
    name = norm_name(app.name)
    title = norm_name(win.title)
    exe = win.exe.casefold().removesuffix(".exe")
    return bool(name) and (title == name or title.endswith(" - " + name) or title.endswith(" – " + name)
                           or exe == name.replace(" ", "") or title.startswith(name + " - "))


def find_windows(windows: list[WindowInfo], query: str) -> list[WindowInfo]:
    """Windows whose app/title matches a spoken name ("chrome", "the excel window", "budget.xlsx")."""
    from rapidfuzz import fuzz

    q = norm_name(query)
    if not q:
        return []
    scored = []
    for w in windows:
        exe = w.exe.casefold().removesuffix(".exe")
        title = norm_name(w.title)
        s = max(100 if exe == q.replace(" ", "") else 0,
                95 if q in title.split(" - ")[-1:] or title.endswith(q) else 0,
                90 if q in title else 0,
                fuzz.partial_ratio(q, title) * 0.85,
                fuzz.ratio(q, exe) * 0.9)
        if s >= 70:
            scored.append((s, w))
    scored.sort(key=lambda x: x[0], reverse=True)
    return [w for _, w in scored]


# ----------------------------------------------------------------------------- desktops

class DesktopGuard:
    """Run something that may steal focus or switch desktops, then put the user back.

    Remembers the current virtual desktop and foreground window; afterwards, if
    Windows switched desktops (apps that activate their window do that), switch
    back and refocus what the user was using.
    """

    def __init__(self, platform: Platform):
        self.p = platform
        self.desktop: int | None = None
        self.foreground: WindowInfo | None = None

    def __enter__(self) -> DesktopGuard:
        try:
            self.desktop = next((d.number for d in self.p.desktops.list() if d.current), None)
        except Exception as e:
            log.debug("desktop guard: no desktop info (%s)", e)
        try:
            self.foreground = self.p.windows.foreground()
        except Exception:
            self.foreground = None
        return self

    def restore(self) -> None:
        try:
            if self.desktop is not None:
                now = next((d.number for d in self.p.desktops.list() if d.current), None)
                if now is not None and now != self.desktop:
                    self.p.desktops.switch(self.desktop)
        except Exception as e:
            log.debug("desktop guard: switch back failed (%s)", e)
        if self.foreground is not None:
            try:
                self.p.windows.focus(self.foreground.hwnd)
            except Exception as e:
                log.debug("desktop guard: refocus failed (%s)", e)

    def __exit__(self, *exc: object) -> None:
        self.restore()


def assistant_desktop(p: Platform, name: str, *, create: bool = True) -> int:
    """Number of the dedicated desktop (created and named on first use)."""
    desks = p.desktops.list()
    for d in desks:
        if d.name.strip().casefold() == name.strip().casefold():
            return d.number
    if not create:
        raise ToolError(f"There is no '{name}' desktop.")
    return p.desktops.create(name).number


def wait_for_window(p: Platform, app: AppEntry, before: set[int], timeout: float,
                    cancel: CancelToken, poll: float = 0.15) -> WindowInfo | None:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        for w in p.windows.list_windows():
            if w.hwnd not in before and window_matches(w, app):
                return w
        if cancel.wait(poll):
            return None
    return None


# ----------------------------------------------------------------------------- services

class AppService:
    """Cached app index + config lookups shared by the tools and the fast path."""

    INDEX_TTL_S = 6 * 3600

    def __init__(self, platform: Platform, config_get: Callable[[str], Any]):
        self.p = platform
        self.cfg = config_get
        self._apps: list[AppEntry] | None = None
        self._loaded = 0.0

    def apps(self, refresh: bool = False) -> list[AppEntry]:
        if refresh or self._apps is None or time.time() - self._loaded > self.INDEX_TTL_S:
            self._apps = self.p.apps.list_apps(refresh=refresh)
            self._loaded = time.time()
        return self._apps

    def matcher(self) -> AppMatcher:
        aliases = dict(self.cfg("apps.aliases") or {})
        music = self.cfg("apps.music_app")
        if music:
            aliases.setdefault("music", music)
            aliases.setdefault("musik", music)
        browser = self.cfg("apps.browser")
        if browser:
            aliases.setdefault("browser", browser)
            aliases.setdefault("internet", browser)
        return AppMatcher(self.apps(), aliases)

    def resolve(self, query: str) -> AppEntry:
        app, alts = self.matcher().match(query)
        if app is None:
            if not alts and self._apps is not None and time.time() - self._loaded > 60:
                app, alts = AppMatcher(self.apps(refresh=True), self.cfg("apps.aliases") or {}).match(query)
            if app is None:
                hint = f" Did you mean: {', '.join(a.name for a in alts)}?" if alts else ""
                raise ToolError(f"I couldn't find an app called “{query}”.{hint}")
        return app

    def desktop_rule(self, app: AppEntry) -> str:
        if not self.cfg("desktops.enabled"):
            return "current"
        rules = {norm_name(k): v for k, v in (self.cfg("desktops.rules") or {}).items()}
        return rules.get(norm_name(app.name), self.cfg("desktops.default_rule") or "current")


def _svc(ctx: ToolContext) -> AppService:
    svc = ctx.services.get("apps")
    if svc is None:
        raise ToolError("app service not available")
    return svc


# ----------------------------------------------------------------------------- open_app

def _plan_open_app(a: dict[str, Any], ctx: ToolContext) -> Plan:
    svc = _svc(ctx)
    app = svc.resolve(a["name"])
    rule = a.get("desktop") or svc.desktop_rule(app)
    where = " on the Assistant desktop" if rule == "assistant" else ""
    return Plan(f"Open {app.name}{where}", [f"app: {app.name} ({app.app_id})"], capability="apps.open",
                state={"app": app, "rule": rule})


def open_app(ctx: ToolContext, app: AppEntry, rule: str, new_window: bool = False) -> str:
    """Launch or focus ``app`` according to ``rule``; returns a short description of what happened."""
    p = ctx.platform
    cancel = ctx.task.cancel
    existing = [w for w in p.windows.list_windows() if window_matches(w, app)]
    if rule == "assistant":
        name = ctx.services["config_get"]("desktops.name") or "Assistant"
        with DesktopGuard(p):
            try:
                target = assistant_desktop(p, name)
            except Exception as e:  # virtual desktop APIs are undocumented; fall back gracefully
                log.warning("assistant desktop unavailable (%s); opening on the current desktop", e)
                target = None
            if existing and not new_window:
                w = existing[0]
                if target is not None and p.desktops.window_desktop(w.hwnd) != target:
                    p.desktops.move_window(w.hwnd, target)
                return f"{app.name} is running on the {name} desktop." if target else f"{app.name} is running."
            before = {w.hwnd for w in p.windows.list_windows()}
            p.apps.launch(app)
            win = wait_for_window(p, app, before, 12.0, cancel)
            if win is not None and target is not None:
                p.desktops.move_window(win.hwnd, target)
                return f"Opened {app.name} on the {name} desktop."
            if win is None:
                return f"Started {app.name} (its window hasn't appeared yet)."
            return f"Opened {app.name}."
    # current desktop
    if existing and not new_window:
        w = existing[0]
        try:
            cur = next((d.number for d in p.desktops.list() if d.current), None)
            if cur is not None and p.desktops.window_desktop(w.hwnd) not in (None, cur):
                p.desktops.move_window(w.hwnd, cur)
        except Exception as e:
            log.debug("could not bring window to current desktop: %s", e)
        p.windows.focus(w.hwnd)
        return f"Switched to {app.name}."
    p.apps.launch(app)
    return f"Opened {app.name}."


def _run_open_app(a: dict[str, Any], ctx: ToolContext, plan: Plan) -> ToolResult:
    msg = open_app(ctx, plan.state["app"], plan.state["rule"], a["new_window"])
    return ToolResult(True, msg, data={"app": plan.state["app"].name})


def _plan_open_url(a: dict[str, Any], ctx: ToolContext) -> Plan:
    url = a["url"].strip()
    if not re.match(r"^https?://", url, re.I):
        if re.match(r"^[\w-]+(\.[\w-]+)+(/.*)?$", url):
            url = "https://" + url
        else:
            raise ToolError("Only http(s) web addresses can be opened.")
    return Plan(f"Open website {url}", [f"url: {url}"], capability="apps.open", state={"url": url})


def _run_open_url(a: dict[str, Any], ctx: ToolContext, plan: Plan) -> ToolResult:
    ctx.platform.apps.open_target(plan.state["url"])
    return ToolResult(True, f"Opened {plan.state['url']}.")


# ----------------------------------------------------------------------------- window

def _resolve_window(ctx: ToolContext, target: str | None) -> WindowInfo:
    p = ctx.platform
    if not target:
        w = p.windows.foreground()
        if w is None:
            raise ToolError("There is no active window.")
        return w
    wins = p.windows.list_windows()
    try:
        app = _svc(ctx).matcher().match(target)[0]
    except Exception:
        app = None
    if app is not None:
        by_app = [w for w in wins if window_matches(w, app)]
        if by_app:
            return by_app[0]
    found = find_windows(wins, target)
    if not found:
        raise ToolError(f"No open window matches “{target}”.")
    return found[0]


def _plan_window(a: dict[str, Any], ctx: ToolContext) -> Plan:
    action = a["action"]
    if action == "list":
        return Plan("List open windows", capability="apps.windows")
    w = _resolve_window(ctx, a.get("target"))
    label = f"{w.title or w.exe} ({w.exe})"
    if action == "close":
        risks = {Risk.CLOSE_UNSAVED} if UNSAVED_MARKERS.search(w.title or "") else set()
        details = [f"window: {label}"]
        if risks:
            details.append("the title suggests unsaved changes")
        return Plan("Close window", details, risks=risks, capability="apps.close", state={"win": w})
    if action == "kill":
        return Plan("Force-close app (unsaved work is lost)", [f"process: {w.exe} (pid {w.pid})", f"window: {label}"],
                    risks={Risk.FORCE_CLOSE}, capability="apps.close", state={"win": w})
    return Plan(f"{action.capitalize()} window", [f"window: {label}"], capability="apps.windows", state={"win": w})


def _run_window(a: dict[str, Any], ctx: ToolContext, plan: Plan) -> ToolResult:
    p, action = ctx.platform, a["action"]
    if action == "list":
        wins = p.windows.list_windows()
        lines = [f"- {w.title or '(untitled)'} [{w.exe}]{' (minimized)' if w.minimized else ''}"
                 f"{f' (desktop {w.desktop})' if w.desktop else ''}" for w in wins[:40]]
        return ToolResult(True, "Open windows:\n" + "\n".join(lines) if lines else "No open windows.",
                          data={"windows": [w.title for w in wins]})
    w: WindowInfo = plan.state["win"]
    if action == "focus":
        try:
            cur = next((d.number for d in p.desktops.list() if d.current), None)
            if cur is not None and p.desktops.window_desktop(w.hwnd) not in (None, cur):
                p.desktops.move_window(w.hwnd, cur)
        except Exception as e:
            log.debug("focus: desktop move skipped (%s)", e)
        if not p.windows.focus(w.hwnd):
            return ToolResult(False, f"Windows didn't let me focus {w.title}.")
        return ToolResult(True, f"Focused {w.title or w.exe}.")
    if action == "close":
        p.windows.close(w.hwnd)
        return ToolResult(True, f"Asked {w.title or w.exe} to close (it may ask to save).")
    if action == "kill":
        p.windows.kill(w.pid)
        return ToolResult(True, f"Force-closed {w.exe}.")
    p.windows.set_state(w.hwnd, action)
    return ToolResult(True, f"{action.capitalize()}d {w.title or w.exe}.")


# ----------------------------------------------------------------------------- desktop

def _plan_desktop(a: dict[str, Any], ctx: ToolContext) -> Plan:
    action, target = a["action"], (a.get("target") or "").strip()
    if action in ("switch", "move_window") and not target:
        raise ToolError("Say which desktop (number, name, 'next', 'previous' or 'assistant').")
    text = {"list": "List virtual desktops", "switch": f"Switch to desktop {target}",
            "create": f"Create desktop {target or ''}".strip(), "move_window": f"Move window to desktop {target}"}
    return Plan(text[action], capability="desktops")


def _desktop_number(ctx: ToolContext, target: str) -> int:
    p = ctx.platform
    desks = p.desktops.list()
    t = target.strip().casefold()
    cur = next((d.number for d in desks if d.current), 1)
    if t in ("next", "nächster", "nächsten", "right", "rechts"):
        return min(cur + 1, len(desks))
    if t in ("previous", "prev", "vorheriger", "vorherigen", "left", "links", "back", "zurück"):
        return max(cur - 1, 1)
    if t in ("assistant", "assistent", (ctx.services["config_get"]("desktops.name") or "").casefold()):
        return assistant_desktop(ctx.platform, ctx.services["config_get"]("desktops.name") or "Assistant")
    words = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "eins": 1, "zwei": 2, "drei": 3, "vier": 4,
             "fünf": 5, "first": 1, "erster": 1, "erste": 1, "second": 2, "zweiter": 2, "third": 3, "dritter": 3}
    num = int(t) if t.isdigit() else words.get(t)
    if num is not None:
        if not 1 <= num <= len(desks):
            raise ToolError(f"There are only {len(desks)} desktops.")
        return num
    for d in desks:
        if d.name.casefold() == t:
            return d.number
    raise ToolError(f"No desktop called “{target}”.")


def _run_desktop(a: dict[str, Any], ctx: ToolContext, plan: Plan) -> ToolResult:
    p, action = ctx.platform, a["action"]
    if action == "list":
        desks = p.desktops.list()
        lines = [f"{d.number}. {d.name or f'Desktop {d.number}'}{' (current)' if d.current else ''}" for d in desks]
        return ToolResult(True, "\n".join(lines))
    if action == "create":
        d = p.desktops.create(a.get("target") or "")
        return ToolResult(True, f"Created desktop {d.number}{f' ({d.name})' if d.name else ''}.")
    num = _desktop_number(ctx, a["target"])
    if action == "switch":
        p.desktops.switch(num)
        return ToolResult(True, f"Switched to desktop {num}.")
    w = p.windows.foreground()
    if w is None:
        raise ToolError("There is no active window to move.")
    p.desktops.move_window(w.hwnd, num)
    return ToolResult(True, f"Moved {w.title or w.exe} to desktop {num}.")


def register(reg: ToolRegistry) -> None:
    reg.register(Tool(
        "open_app", "Open (or switch to) an installed app by name. Respects the user's desktop rules.",
        obj(("name",), name=Str("App name as the user said it, e.g. 'Spotify', 'Word', 'browser'", min_len=1,
                                max_len=100),
            desktop=Str("Override where it opens", enum=("current", "assistant")),
            new_window=Bool("Open a new window even if it is already running", default=False)),
        "apps.open", _plan_open_app, _run_open_app))
    reg.register(Tool(
        "open_url", "Open a web page in the default browser.",
        obj(("url",), url=Str("http(s) address", min_len=3, max_len=2000)),
        "apps.open", _plan_open_url, _run_open_url))
    reg.register(Tool(
        "window", "Manage app windows: focus, close (app may ask to save), minimize, maximize, restore, list, "
                  "kill (force-close).",
        obj(("action",), action=Str("What to do", enum=("focus", "close", "minimize", "maximize", "restore",
                                                         "list", "kill")),
            target=Str("App name or window title; empty = the active window", max_len=200)),
        "apps.windows", _plan_window, _run_window))
    reg.register(Tool(
        "desktop", "Virtual desktops: list, switch (number/name/next/previous/assistant), create, move the "
                   "active window to another desktop.",
        obj(("action",), action=Str("What to do", enum=("list", "switch", "create", "move_window")),
            target=Str("Desktop number, name, 'next', 'previous' or 'assistant'", max_len=60)),
        "desktops", _plan_desktop, _run_desktop))
