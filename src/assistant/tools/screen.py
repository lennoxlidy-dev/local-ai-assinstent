"""Mouse/keyboard and screen tools.

Preferred order for "click the Save button": UI Automation (find the control
by name, invoke it without moving the mouse) -> OCR (find the text on screen,
click its center) -> the model's vision (milestone 3). Coordinates are
physical pixels (the process is per-monitor DPI aware).
"""

from __future__ import annotations

import logging
import re
from typing import Any

from .apps import _resolve_window
from .base import Plan, Tool, ToolContext, ToolError, ToolRegistry, ToolResult
from .platform import OcrLine, UiElement
from .schema import Arr, Int, Str, obj

log = logging.getLogger(__name__)

KEY_NAMES = {
    "ctrl", "control", "strg", "alt", "shift", "win", "windows", "cmd", "enter", "return", "tab", "esc", "escape",
    "space", "backspace", "delete", "del", "entf", "insert", "ins", "home", "end", "pageup", "pagedown", "pgup",
    "pgdn", "up", "down", "left", "right", "capslock", "printscreen", "prtsc", "menu", "apps",
    "volumeup", "volumedown", "volumemute", "playpause", "nexttrack", "prevtrack", "mediastop",
    *(f"f{i}" for i in range(1, 25)),
}


def parse_keys(spec: str | list[str]) -> list[str]:
    """'ctrl+shift+s' / ['ctrl','s'] -> normalized key names; validates every key."""
    parts = spec if isinstance(spec, list) else re.split(r"\s*\+\s*|\s+", spec.strip())
    keys = []
    for k in parts:
        k = k.strip().lower()
        if not k:
            continue
        alias = {"control": "ctrl", "strg": "ctrl", "windows": "win", "cmd": "win", "return": "enter",
                 "escape": "esc", "del": "delete", "entf": "delete", "ins": "insert", "pgup": "pageup",
                 "pgdn": "pagedown", "prtsc": "printscreen", "apps": "menu"}.get(k, k)
        if alias not in KEY_NAMES and not re.fullmatch(r"[a-z0-9]|[`\-=\[\]\\;',./]", alias):
            raise ToolError(f"Unknown key “{k}”.")
        keys.append(alias)
    if not keys:
        raise ToolError("No keys given.")
    return keys


# ----------------------------------------------------------------------------- mouse

def _plan_mouse(a: dict[str, Any], ctx: ToolContext) -> Plan:
    action = a["action"]
    needs_xy = action in ("move", "drag")
    if needs_xy and ("x" not in a or "y" not in a):
        raise ToolError("x and y are required.")
    if action == "drag" and ("to_x" not in a or "to_y" not in a):
        raise ToolError("to_x and to_y are required for drag.")
    where = f" at ({a['x']}, {a['y']})" if "x" in a and "y" in a else " at the current position"
    if action == "drag":
        where = f" from ({a['x']}, {a['y']}) to ({a['to_x']}, {a['to_y']})"
    if action == "scroll":
        where = f" by {a.get('amount', -3)}" + (f" at ({a['x']}, {a['y']})" if "x" in a else "")
    return Plan(f"Mouse {action.replace('_', ' ')}{where}", capability="input")


def _check_on_screen(ctx: ToolContext, x: int, y: int) -> None:
    sx, sy, sw, sh = ctx.platform.input.screen_rect()
    if not (sx <= x < sx + sw and sy <= y < sy + sh):
        raise ToolError(f"({x}, {y}) is outside the screen ({sx},{sy} {sw}x{sh}).")


def _run_mouse(a: dict[str, Any], ctx: ToolContext, plan: Plan) -> ToolResult:
    inp, action = ctx.platform.input, a["action"]
    x, y = a.get("x"), a.get("y")
    if x is not None and y is not None:
        _check_on_screen(ctx, x, y)
    if action == "move":
        assert x is not None and y is not None  # checked in _plan_mouse
        inp.move(x, y)
    elif action in ("click", "double_click", "right_click", "middle_click"):
        button = {"right_click": "right", "middle_click": "middle"}.get(action, "left")
        inp.click(x, y, button, 2 if action == "double_click" else 1)
    elif action == "drag":
        assert x is not None and y is not None
        _check_on_screen(ctx, a["to_x"], a["to_y"])
        inp.drag(x, y, a["to_x"], a["to_y"])
    elif action == "scroll":
        inp.scroll(a.get("amount", -3), x, y)
    return ToolResult(True, "Done.")


# ----------------------------------------------------------------------------- keyboard

def _plan_keyboard(a: dict[str, Any], ctx: ToolContext) -> Plan:
    action = a["action"]
    target = _resolve_window(ctx, a["window"]) if a.get("window") else ctx.platform.windows.foreground()
    where = f"{target.title or target.exe}" if target else "the active window"
    if action == "type":
        if not a.get("text"):
            raise ToolError("text is required.")
        shown = a["text"] if len(a["text"]) <= 120 else a["text"][:117] + "..."
        return Plan("Type text", [f"into: {where}", f"text: {shown}"], capability="input",
                    state={"win": target if a.get("window") else None})
    keys = parse_keys(a.get("keys") or a.get("text") or "")
    return Plan(f"Press {'+'.join(keys)}", [f"in: {where}"], capability="input",
                state={"keys": keys, "win": target if a.get("window") else None})


def _run_keyboard(a: dict[str, Any], ctx: ToolContext, plan: Plan) -> ToolResult:
    inp = ctx.platform.input
    win = plan.state.get("win")
    if win is not None:  # focus the target right before typing (after the confirmation prompt)
        if not ctx.platform.windows.focus(win.hwnd):
            raise ToolError(f"Couldn't switch to {win.title}; not typing anything.")
        ctx.task.cancel.wait(0.15)
    if a["action"] == "type":
        inp.type_text(a["text"], ctx.task.cancel)
        return ToolResult(True, f"Typed {len(a['text'])} characters.")
    inp.hotkey(plan.state["keys"])
    return ToolResult(True, f"Pressed {'+'.join(plan.state['keys'])}.")


# ----------------------------------------------------------------------------- UI Automation

def _window_hwnd(ctx: ToolContext, target: str | None) -> int | None:
    return _resolve_window(ctx, target).hwnd if target else None


def _plan_elements(a: dict[str, Any], ctx: ToolContext) -> Plan:
    return Plan(f"List controls in {a.get('window') or 'the active window'}", capability="screen.capture")


def _run_elements(a: dict[str, Any], ctx: ToolContext, plan: Plan) -> ToolResult:
    els = ctx.platform.uia.elements(_window_hwnd(ctx, a.get("window")), 300, ctx.task.cancel)
    flt = (a.get("filter") or "").casefold()
    els = [e for e in els if e.name.strip() and (not flt or flt in e.name.casefold())]
    lines = [f"- {e.control_type.replace('Control', '')} “{e.name[:80]}” at ({e.center[0]}, {e.center[1]})"
             f"{'' if e.enabled else ' [disabled]'}" for e in els[: a["limit"]]]
    if not lines:
        return ToolResult(True, "No named controls found (the app may not support UI Automation; try read_screen).")
    return ToolResult(True, "Controls:\n" + "\n".join(lines), untrusted=True)


def best_element(elements: list[UiElement], name: str, control_type: str | None = None) -> UiElement | None:
    from rapidfuzz import fuzz

    q = name.casefold().strip()
    best, best_score = None, 0.0
    for e in elements:
        if not e.enabled or not e.name.strip():
            continue
        if control_type and control_type.lower() not in e.control_type.lower():
            continue
        n = e.name.casefold().strip()
        score = 100.0 if n == q else 92.0 if n.startswith(q) else fuzz.ratio(q, n) * 0.9
        if "button" in e.control_type.lower() or "menuitem" in e.control_type.lower():
            score += 2
        if score > best_score:
            best, best_score = e, score
    return best if best_score >= 75 else None


def _plan_click_element(a: dict[str, Any], ctx: ToolContext) -> Plan:
    return Plan(f"Click “{a['name']}” in {a.get('window') or 'the active window'}", capability="input")


def _run_click_element(a: dict[str, Any], ctx: ToolContext, plan: Plan) -> ToolResult:
    p = ctx.platform
    hwnd = _window_hwnd(ctx, a.get("window"))
    try:
        els = p.uia.elements(hwnd, 600, ctx.task.cancel)
    except ToolError:
        els = []
    el = best_element(els, a["name"], a.get("control_type"))
    if el is not None:
        how = p.uia.activate(el, hwnd)
        return ToolResult(True, f"Clicked “{el.name}” ({how}).")
    # OCR fallback: find the text on screen and click it
    try:
        png, _, _ = p.screen.screenshot_png()
        lines = p.screen.ocr(png, ctx.services["config_get"]("general.language") or "en")
    except ToolError:
        lines = []
    hit = best_ocr_match(lines, a["name"])
    if hit is None:
        raise ToolError(f"I couldn't find “{a['name']}” on the screen.")
    x, y = hit.x + hit.w // 2, hit.y + hit.h // 2
    p.input.click(x, y, "left", 1)
    return ToolResult(True, f"Clicked the text “{hit.text}” at ({x}, {y}) (found with OCR).")


def best_ocr_match(lines: list[OcrLine], text: str) -> OcrLine | None:
    from rapidfuzz import fuzz

    q = text.casefold().strip()
    scored = [(100 if ln.text.casefold().strip() == q else fuzz.partial_ratio(q, ln.text.casefold()), ln)
              for ln in lines]
    scored = [s for s in scored if s[0] >= 85]
    scored.sort(key=lambda s: (-s[0], len(s[1].text)))
    return scored[0][1] if scored else None


# ----------------------------------------------------------------------------- screen

def _plan_read_screen(a: dict[str, Any], ctx: ToolContext) -> Plan:
    return Plan("Read the text on the screen (OCR)", capability="screen.capture")


def _run_read_screen(a: dict[str, Any], ctx: ToolContext, plan: Plan) -> ToolResult:
    p = ctx.platform
    region = None
    if a.get("window"):
        w = _resolve_window(ctx, a["window"])
        region = p.windows.rect(w.hwnd)
    png, w_, h_ = p.screen.screenshot_png(region)
    lines = p.screen.ocr(png, ctx.services["config_get"]("general.language") or "en")
    if not lines:
        return ToolResult(True, "I couldn't read any text on the screen.")
    out = [f"({ln.x + ln.w // 2}, {ln.y + ln.h // 2}) {ln.text}" for ln in lines[:200]]
    return ToolResult(True, f"Screen text ({w_}x{h_}, center coordinates):\n" + "\n".join(out), untrusted=True)


def _plan_screenshot(a: dict[str, Any], ctx: ToolContext) -> Plan:
    if a.get("save_to"):
        return Plan("Save a screenshot", [f"file: {a['save_to']}"], [(a["save_to"], "write")],
                    capability="files.write")
    return Plan("Take a screenshot (kept in memory)", capability="screen.capture")


def _run_screenshot(a: dict[str, Any], ctx: ToolContext, plan: Plan) -> ToolResult:
    png, w, h = ctx.platform.screen.screenshot_png()
    ctx.services["last_screenshot"] = png  # for the vision model (milestone 3)
    if a.get("save_to"):
        from pathlib import Path

        target = Path(ctx.path(a["save_to"]).path)
        if target.suffix.lower() != ".png":
            target = target.with_suffix(".png")
        if target.exists():
            raise ToolError(f"{target} already exists.")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(png)
        return ToolResult(True, f"Saved a {w}x{h} screenshot to {target}.", data={"path": str(target)})
    return ToolResult(True, f"Took a {w}x{h} screenshot.", data={"width": w, "height": h})


def register(reg: ToolRegistry) -> None:
    reg.register(Tool(
        "mouse", "Move/click/drag/scroll the mouse at screen coordinates (physical pixels). Prefer click_element.",
        obj(("action",), action=Str("What to do", enum=("move", "click", "double_click", "right_click",
                                                         "middle_click", "drag", "scroll")),
            x=Int("X coordinate"), y=Int("Y coordinate"), to_x=Int("Drag target X"), to_y=Int("Drag target Y"),
            amount=Int("Scroll clicks: negative = down", min=-50, max=50)),
        "input", _plan_mouse, _run_mouse))
    reg.register(Tool(
        "keyboard", "Type text into the active window, or press a key combination like 'ctrl+s'.",
        obj(("action",), action=Str("type or hotkey", enum=("type", "hotkey")),
            text=Str("Text to type", max_len=20000), keys=Str("Keys for hotkey, e.g. 'ctrl+shift+t'", max_len=60),
            window=Str("Focus this app/window first (optional)", max_len=200)),
        "input", _plan_keyboard, _run_keyboard))
    reg.register(Tool(
        "ui_elements", "List the named buttons/fields/menus of a window with their screen coordinates.",
        obj((), window=Str("App name or window title; empty = active window", max_len=200),
            filter=Str("Only names containing this text", max_len=100),
            limit=Int("Max entries", min=1, max=200, default=60)),
        "screen.capture", _plan_elements, _run_elements))
    reg.register(Tool(
        "click_element", "Click a button/link/menu item by its visible name (UI Automation, falls back to OCR).",
        obj(("name",), name=Str("Visible text/name of the control", min_len=1, max_len=200),
            window=Str("App name or window title; empty = active window", max_len=200),
            control_type=Str("Optional type filter, e.g. Button, MenuItem, Edit", max_len=40)),
        "input", _plan_click_element, _run_click_element))
    reg.register(Tool(
        "read_screen", "Read all text visible on screen (or in one window) with coordinates, using OCR.",
        obj((), window=Str("App name or window title; empty = whole screen", max_len=200)),
        "screen.capture", _plan_read_screen, _run_read_screen))
    reg.register(Tool(
        "screenshot", "Take a screenshot; optionally save it as PNG.",
        obj((), save_to=Str("File path to save to (optional)", max_len=1000)),
        "screen.capture", _plan_screenshot, _run_screenshot))


__all__ = ["Arr", "best_element", "best_ocr_match", "parse_keys", "register"]
