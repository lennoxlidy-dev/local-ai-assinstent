"""look_at_screen: let a vision-capable model answer questions about the screen.

Last resort after UI Automation and OCR. The screenshot never leaves the PC
(it goes to the local llama-server) and is not stored. The model's answer
describes screen content, so it is untrusted data like OCR text.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

from ..tools.base import Plan, Tool, ToolContext, ToolError, ToolRegistry, ToolResult
from ..tools.schema import Str, obj

if TYPE_CHECKING:
    from .service import LlmService

_PCT = re.compile(r"x\s*[=:]\s*(\d+(?:\.\d+)?)\s*%?.*?y\s*[=:]\s*(\d+(?:\.\d+)?)\s*%?", re.I | re.S)


def parse_location(answer: str) -> tuple[float, float] | None:
    m = _PCT.search(answer)
    if not m:
        return None
    x, y = float(m.group(1)), float(m.group(2))
    if not (0 <= x <= 100 and 0 <= y <= 100):
        return None
    return x, y


def register(reg: ToolRegistry, llm: LlmService) -> None:
    def plan(a: dict[str, Any], ctx: ToolContext) -> Plan:
        return Plan("Look at the screen with the vision model", capability="screen.capture")

    def run(a: dict[str, Any], ctx: ToolContext, p: Plan) -> ToolResult:
        if llm.spec is not None and not llm.spec.vision:
            raise ToolError("The current model has no vision; use read_screen (OCR) or ui_elements instead.")
        png, w, h = ctx.platform.screen.screenshot_png()
        if a.get("locate"):
            q = (f"Find this on the screenshot: {a['locate']}. Answer ONLY with its center as percentages of the "
                 "image width and height, like: x=42% y=17%. If it is not visible, answer: not found.")
            answer = llm.describe_image(png, q, cancel=ctx.task.cancel)
            loc = parse_location(answer)
            if loc is None:
                return ToolResult(False, f"I couldn't locate “{a['locate']}” on the screen.", untrusted=True)
            sx, sy, _, _ = ctx.platform.input.screen_rect()
            x, y = int(sx + loc[0] / 100 * w), int(sy + loc[1] / 100 * h)
            return ToolResult(True, f"“{a['locate']}” appears to be at ({x}, {y}) (estimated by vision; verify "
                                    "if the click matters).", data={"x": x, "y": y}, untrusted=True)
        answer = llm.describe_image(png, a.get("question") or "Describe what is on the screen briefly.",
                                    cancel=ctx.task.cancel)
        return ToolResult(True, f"What the screen shows: {answer}", untrusted=True)

    reg.register(Tool(
        "look_at_screen", "Look at the screen with the vision model: answer a question about it, or locate an item "
                          "(returns screen coordinates for the mouse tool). Use after ui_elements/read_screen fail.",
        obj((), question=Str("What you want to know about the screen", max_len=300),
            locate=Str("Something to find, e.g. 'the blue Submit button'", max_len=200)),
        "screen.capture", plan, run))
