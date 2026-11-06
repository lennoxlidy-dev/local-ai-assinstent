"""System tools: volume, brightness, clipboard, power (lock/sleep/shutdown...), timers & reminders."""

from __future__ import annotations

import datetime as dt
import re
import time
from typing import Any

from ..permissions.policy import Risk
from .base import Plan, Tool, ToolContext, ToolError, ToolRegistry, ToolResult
from .schema import Bool, Int, Str, obj


def _clamp(v: int) -> int:
    return max(0, min(100, v))


# ----------------------------------------------------------------------------- volume / brightness

def _plan_volume(a: dict[str, Any], ctx: ToolContext) -> Plan:
    if "level" not in a and "change" not in a and "mute" not in a:
        return Plan("Read volume", capability="system.volume")
    parts = []
    if "level" in a:
        parts.append(f"set to {a['level']}%")
    if "change" in a:
        parts.append(f"change by {a['change']:+d}")
    if "mute" in a:
        parts.append("mute" if a["mute"] else "unmute")
    return Plan("Volume: " + ", ".join(parts), capability="system.volume")


def _run_volume(a: dict[str, Any], ctx: ToolContext, plan: Plan) -> ToolResult:
    au = ctx.platform.audio
    if "level" in a or "change" in a:
        base = a["level"] if "level" in a else au.get_volume()
        new = _clamp(base + a.get("change", 0))
        au.set_volume(new)
        if new > 0 and "mute" not in a and au.get_mute():
            au.set_mute(False)
    if "mute" in a:
        au.set_mute(a["mute"])
    vol, muted = au.get_volume(), au.get_mute()
    return ToolResult(True, f"Volume {vol}%{' (muted)' if muted else ''}.", data={"volume": vol, "muted": muted})


def _plan_brightness(a: dict[str, Any], ctx: ToolContext) -> Plan:
    what = f"set to {a['level']}%" if "level" in a else (f"change by {a['change']:+d}" if "change" in a else "read")
    return Plan(f"Brightness: {what}", capability="system.display")


def _run_brightness(a: dict[str, Any], ctx: ToolContext, plan: Plan) -> ToolResult:
    d = ctx.platform.display
    if "level" in a or "change" in a:
        cur = d.get_brightness()
        if cur is None and "level" not in a:
            raise ToolError("I can't read the current brightness of this display.")
        new = _clamp(a["level"] if "level" in a else (cur or 0) + a["change"])
        d.set_brightness(new)
        return ToolResult(True, f"Brightness {new}%.", data={"brightness": new})
    cur = d.get_brightness()
    return ToolResult(True, f"Brightness {cur}%." if cur is not None else "Brightness is not available.")


# ----------------------------------------------------------------------------- clipboard

def _plan_clip(a: dict[str, Any], ctx: ToolContext) -> Plan:
    if a["action"] == "set":
        if "text" not in a:
            raise ToolError("text is required to set the clipboard")
        return Plan("Copy text to the clipboard", [f"{len(a['text'])} characters"], capability="system.clipboard")
    return Plan("Read the clipboard", capability="system.clipboard")


def _run_clip(a: dict[str, Any], ctx: ToolContext, plan: Plan) -> ToolResult:
    if a["action"] == "set":
        ctx.platform.clipboard.set_text(a["text"])
        return ToolResult(True, "Copied to the clipboard.")
    text = ctx.platform.clipboard.get_text()
    if not text:
        return ToolResult(True, "The clipboard has no text.")
    clipped = text[:8000]
    return ToolResult(True, f"Clipboard text{' (truncated)' if len(text) > 8000 else ''}:\n{clipped}",
                      untrusted=True)


# ----------------------------------------------------------------------------- power

def _plan_power(a: dict[str, Any], ctx: ToolContext) -> Plan:
    action = a["action"]
    if action == "lock":
        return Plan("Lock the PC", capability="system.lock")
    labels = {"sleep": "Put the PC to sleep", "shutdown": "Shut down the PC", "restart": "Restart the PC",
              "sign_out": "Sign out of Windows"}
    return Plan(labels[action], ["unsaved work in open apps may be lost" if action != "sleep" else ""],
                risks={Risk.POWER}, capability="system.power")


def _run_power(a: dict[str, Any], ctx: ToolContext, plan: Plan) -> ToolResult:
    getattr(ctx.platform.power, a["action"])()
    return ToolResult(True, {"lock": "Locked.", "sleep": "Going to sleep.", "shutdown": "Shutting down.",
                             "restart": "Restarting.", "sign_out": "Signing out."}[a["action"]])


# ----------------------------------------------------------------------------- timers & reminders

_DUR = re.compile(r"(\d+(?:[.,]\d+)?)\s*(h|hours?|stunden?|std|m|min|mins|minutes?|minuten?|s|sec|secs|seconds?|"
                  r"sekunden?)\b", re.I)


def parse_duration(text: str) -> int | None:
    """'5 min', '1h 30m', '90 seconds', '2 Stunden' -> seconds."""
    total, found = 0.0, False
    for num, unit in _DUR.findall(text):
        n = float(num.replace(",", "."))
        u = unit.lower()
        mult = 3600 if u.startswith(("h", "std", "stund")) else 60 if u.startswith("m") else 1
        total += n * mult
        found = True
    return int(total) if found else None


def parse_time_of_day(text: str, now: dt.datetime | None = None) -> dt.datetime | None:
    """'17:30', '5 pm', '5:15pm', ISO '2026-11-06T17:00' -> next such local time."""
    now = now or dt.datetime.now()
    t = text.strip().lower()
    try:
        when = dt.datetime.fromisoformat(t.upper() if "t" in t else t)
        return when if when.tzinfo is None else when.astimezone().replace(tzinfo=None)
    except ValueError:
        pass
    m = re.fullmatch(r"(\d{1,2})(?:[:.](\d{2}))?\s*(am|pm|uhr)?", t)
    if not m:
        return None
    h, mi = int(m.group(1)), int(m.group(2) or 0)
    if m.group(3) == "pm" and h < 12:
        h += 12
    if m.group(3) == "am" and h == 12:
        h = 0
    if not (0 <= h < 24 and 0 <= mi < 60):
        return None
    when = now.replace(hour=h, minute=mi, second=0, microsecond=0)
    if when <= now:
        when += dt.timedelta(days=1)
    return when


def _plan_timer(a: dict[str, Any], ctx: ToolContext) -> Plan:
    action = a["action"]
    if action == "set_timer":
        secs = a.get("seconds") or (parse_duration(a.get("duration", "")) if a.get("duration") else None)
        if not secs:
            raise ToolError("How long? Give seconds or a duration like '5 min'.")
        return Plan(f"Timer for {_human(secs)}", capability="system.notify", state={"due": time.time() + secs})
    if action == "set_reminder":
        if not a.get("at"):
            raise ToolError("When? Give a time like '17:30' or an ISO date-time.")
        when = parse_time_of_day(a["at"])
        if when is None:
            secs = parse_duration(a["at"])
            when = dt.datetime.now() + dt.timedelta(seconds=secs) if secs else None
        if when is None:
            raise ToolError(f"I don't understand the time “{a['at']}”.")
        if not a.get("label"):
            raise ToolError("What should I remind you about?")
        return Plan(f"Reminder at {when:%Y-%m-%d %H:%M}: {a['label']}", capability="system.notify",
                    state={"due": when.timestamp()})
    return Plan("Timers: " + action, capability="system.notify")


def _human(secs: float) -> str:
    secs = int(round(secs))
    h, rem = divmod(secs, 3600)
    m, s = divmod(rem, 60)
    parts = [f"{h} h" if h else "", f"{m} min" if m else "", f"{s} s" if s and not h else ""]
    return " ".join(p for p in parts if p) or "0 s"


def _run_timer(a: dict[str, Any], ctx: ToolContext, plan: Plan) -> ToolResult:
    svc = ctx.services.get("timers")
    if svc is None:
        raise ToolError("timers are not available")
    action = a["action"]
    if action in ("set_timer", "set_reminder"):
        kind = "timer" if action == "set_timer" else "reminder"
        e = svc.add(kind, a.get("label", ""), plan.state["due"])
        if kind == "timer":
            return ToolResult(True, f"Timer set for {_human(e.remaining_s)}.", data={"id": e.id})
        due = dt.datetime.fromtimestamp(e.due)
        day = "" if due.date() == dt.date.today() else f" on {due:%a %d %b}"
        return ToolResult(True, f"I'll remind you at {due:%H:%M}{day}: {e.label}.", data={"id": e.id})
    pending = svc.pending()
    if action == "list":
        if not pending:
            return ToolResult(True, "No timers or reminders.")
        lines = [f"#{t.id} {t.kind} {t.label or ''} - {_human(t.remaining_s)} left" for t in pending]
        return ToolResult(True, "\n".join(lines))
    # cancel
    target = pending
    if "id" in a:
        target = [t for t in pending if t.id == a["id"]]
    elif a.get("label"):
        target = [t for t in pending if a["label"].lower() in t.label.lower()]
    elif len(pending) > 1 and not a.get("all"):
        raise ToolError("Several timers are running; which one? (id or label, or all=true)")
    if not target:
        return ToolResult(False, "No matching timer.")
    for t in target:
        svc.cancel(t.id)
    return ToolResult(True, f"Cancelled {len(target)} timer(s).")


def register(reg: ToolRegistry) -> None:
    reg.register(Tool(
        "volume", "Get or change the system volume.",
        obj((), level=Int("Set to this percent", min=0, max=100), change=Int("Change by +/- percent", min=-100,
                                                                             max=100),
            mute=Bool("Mute (true) or unmute (false)")),
        "system.volume", _plan_volume, _run_volume))
    reg.register(Tool(
        "brightness", "Get or change the screen brightness (laptop display).",
        obj((), level=Int("Set to this percent", min=0, max=100), change=Int("Change by +/- percent", min=-100,
                                                                             max=100)),
        "system.display", _plan_brightness, _run_brightness))
    reg.register(Tool(
        "clipboard", "Read the clipboard text, or copy text to it.",
        obj(("action",), action=Str("get or set", enum=("get", "set")), text=Str("Text to copy", max_len=100000)),
        "system.clipboard", _plan_clip, _run_clip))
    reg.register(Tool(
        "power", "Lock, sleep, shut down, restart or sign out the PC - only when the user explicitly asks for "
                 "exactly that (never to 'close' an app).",
        obj(("action",), action=Str("What to do", enum=("lock", "sleep", "shutdown", "restart", "sign_out"))),
        "system.power", _plan_power, _run_power))
    reg.register(Tool(
        "timer", "Timers and reminders (shown as Windows notifications).",
        obj(("action",), action=Str("What to do", enum=("set_timer", "set_reminder", "list", "cancel")),
            seconds=Int("Timer length in seconds", min=1, max=7 * 86400),
            duration=Str("Timer length as text, e.g. '5 min'", max_len=40),
            at=Str("Reminder time: '17:30', '5 pm' or ISO date-time", max_len=40),
            label=Str("What it is for", max_len=200),
            id=Int("Timer id to cancel", min=1), all=Bool("Cancel all")),
        "system.notify", _plan_timer, _run_timer))
