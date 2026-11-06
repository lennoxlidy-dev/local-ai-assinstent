"""The agent loop: LLM <-> tools, multi-step, with a step limit and progress events.

Every tool call goes through the same ``ToolRegistry.invoke`` path as
everything else (validation, permission gate, action log). Results from
untrusted sources are wrapped in per-task random markers, and the task is
"tainted" so side-effecting follow-up actions need confirmation.
"""

from __future__ import annotations

import json
import logging
import re
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..core.events import EventBus
from ..core.timing import Trace
from ..permissions.gate import Task
from .client import ChatResult, LLMError
from .prompts import new_marker, user_message, wrap_untrusted

if TYPE_CHECKING:
    from ..tools.runtime import ToolSystem
    from .service import LlmService

log = logging.getLogger(__name__)

MAX_TOOL_RESULT_CHARS = 6000
_THINK_TAGS = re.compile(r"<think>.*?</think>\s*", re.S)

#: An answer that claims a finished action. If no tool ran, the model made it up -> one corrective nudge.
_CLAIM = re.compile(
    r"\b(i(?:'ve| have) (?:saved|created|deleted|moved|renamed|opened|closed|set|muted|noted|remembered|started|"
    r"turned|changed|copied|installed|locked)|i(?:'ll| will) remember|(?:has|have) been (?:saved|created|deleted|"
    r"moved|renamed|opened|closed|set|muted|copied|installed)|is now (?:open|closed|set|muted|saved|on|off)|"
    r"ich merke mir|ich habe (?:(?:es|das|dir|mir|die|den|ihn|sie|uns|euch)\s+){0,2}(?:gespeichert|erstellt|gelöscht|verschoben|"
    r"umbenannt|geöffnet|geschlossen|eingestellt|gemerkt|notiert|kopiert|installiert|gesperrt)|wurde (?:jetzt )?"
    r"(?:erstellt|gelöscht|verschoben|umbenannt|gespeichert|geöffnet|geschlossen|gesetzt|eingestellt|kopiert|"
    r"installiert)|ist (?:jetzt )?(?:gespeichert|erstellt|gelöscht|geöffnet|geschlossen|eingestellt|stumm))\b",
    re.I)
DISCLAIMER = {"en": "(Note: I didn't actually run any action for this.)",
              "de": "(Hinweis: Dafür habe ich keine Aktion ausgeführt - es wurde nichts geändert.)"}
NUDGE_CLAIM = ("(Check: you did not call any tool in this request, so nothing has been done yet. If the user asked "
               "for an action, call the right tool now. Otherwise answer again without claiming you did anything.)")


@dataclass
class AgentResult:
    answer: str
    messages: list[dict[str, Any]] = field(default_factory=list)  # new turn messages (for history)
    steps: int = 0
    calls: list[dict[str, Any]] = field(default_factory=list)      # {"name","args","ok","text"}
    thinking: bool = False
    stopped: str = ""                                              # "", "step_limit", "loop"
    first_token_ms: float | None = None


class Agent:
    def __init__(self, llm: LlmService, tools: ToolSystem, *, bus: EventBus | None = None,
                 max_steps: int = 8, tool_filter: set[str] | None = None):
        self.llm = llm
        self.tools = tools
        self.bus = bus
        self.max_steps = max_steps
        self.tool_filter = tool_filter  # offer only these tools (None = all)

    def _emit(self, topic: str, **data: Any) -> None:
        if self.bus is not None:
            self.bus.publish(topic, **data)

    def run(self, text: str, *, system: str, context: str, history: list[dict[str, Any]], task: Task,
            thinking: bool = False, trace: Trace | None = None,
            on_content: Callable[[str], None] | None = None, language: str = "en") -> AgentResult:
        marker = new_marker()
        user = {"role": "user", "content": user_message(text, context)}
        msgs = [{"role": "system", "content": system}, *history, user]
        out = AgentResult(answer="", messages=[user], thinking=thinking)
        schemas = self.tools.registry.schemas(self.tool_filter)
        seen: Counter[tuple[str, str]] = Counter()
        res: ChatResult | None = None
        on_content = ThinkFilter(on_content) if on_content is not None else None
        nudged = False

        for step in range(1, self.max_steps + 1):
            task.cancel.check()
            out.steps = step
            self._emit("agent.step", step=step, task_id=task.id)
            res = self.llm.chat(msgs, tools=schemas, thinking=thinking, on_content=on_content, cancel=task.cancel)
            if out.first_token_ms is None and res.ttft_ms is not None:
                out.first_token_ms = res.ttft_ms
                if trace is not None:
                    trace.mark("llm_first_token")
            if not res.tool_calls:
                out.answer = clean_answer(res.content)
                if out.answer and not out.calls and not nudged and step < self.max_steps \
                        and _CLAIM.search(out.answer):
                    # claimed an action without doing it (small models do this): correct it once
                    log.info("answer claims an action but no tool ran; nudging the model")
                    nudged = True
                    msgs += [{"role": "assistant", "content": out.answer}, {"role": "user", "content": NUDGE_CLAIM}]
                    self._emit("agent.retry", reason="claim_without_tool", task_id=task.id)
                    continue
                if out.answer:
                    if nudged and not out.calls and _CLAIM.search(out.answer):
                        # still claiming after the correction: tell the user the truth instead
                        out.answer += " " + DISCLAIMER.get(language, DISCLAIMER["en"])
                    out.messages.append({"role": "assistant", "content": out.answer})
                    return out
                # empty answer after tools: ask once for a spoken summary
                if step == 1:
                    out.answer = ""
                    return out
                break
            assistant = {"role": "assistant", "content": res.content or "",
                         "tool_calls": [{"id": c.id, "type": "function",
                                         "function": {"name": c.name, "arguments": c.arguments or "{}"}}
                                        for c in res.tool_calls]}
            msgs.append(assistant)
            out.messages.append(assistant)
            looping = False
            for call in res.tool_calls:
                task.cancel.check()
                try:
                    args = call.args()
                except ValueError as e:
                    content, ok, args = f"Error: {e}. Call the tool again with valid JSON arguments.", False, {}
                else:
                    key = (call.name, json.dumps(args, sort_keys=True, ensure_ascii=False))
                    seen[key] += 1
                    if seen[key] > 2:
                        looping = True
                        content, ok = ("You already called this exact tool with these arguments twice. "
                                       "Do not call it again; answer the user now."), False
                    else:
                        self._emit("agent.tool", phase="start", name=call.name, args=args, task_id=task.id)
                        r = self.tools.invoke(call.name, args, task)
                        ok = r.ok
                        body = r.text if len(r.text) <= MAX_TOOL_RESULT_CHARS else (
                            r.text[:MAX_TOOL_RESULT_CHARS] + "\n[…truncated]")
                        content = wrap_untrusted(call.name, body, marker) if r.untrusted else body
                        if not r.ok:  # make failures impossible to misread as success
                            content = f"FAILED (nothing was done): {content}"
                        self._emit("agent.tool", phase="done", name=call.name, ok=r.ok, text=r.text[:200],
                                   task_id=task.id)
                out.calls.append({"name": call.name, "args": args, "ok": ok, "text": content[:300]})
                tool_msg = {"role": "tool", "tool_call_id": call.id, "content": content}
                msgs.append(tool_msg)
                out.messages.append(tool_msg)
            if looping:
                out.stopped = "loop"
                break
        else:
            out.stopped = "step_limit"

        # Out of steps (or looping): one final call asking for a short status, tools still listed so the
        # cached prompt prefix stays valid. If it insists on tools, summarize ourselves.
        nudge = {"role": "user", "content": "(Stop using tools now. In one or two sentences tell the user what "
                                            "you did and what, if anything, is still open.)"}
        msgs.append(nudge)
        try:
            res = self.llm.chat(msgs, tools=schemas, thinking=False, on_content=on_content, cancel=task.cancel)
            out.answer = clean_answer(res.content) if not res.tool_calls else ""
        except LLMError as e:
            log.warning("final summary failed: %s", e)
        if not out.answer:
            done = list(dict.fromkeys(c["name"] for c in out.calls if c["ok"]))
            out.answer = (f"I ran {', '.join(done)} but couldn't finish." if done
                          else "Sorry, I couldn't complete that.")
        out.messages.append({"role": "assistant", "content": out.answer})
        return out


class ThinkFilter:
    """Streaming filter that drops ``<think>…</think>`` blocks a model may print into its content."""

    OPEN, CLOSE = "<think>", "</think>"

    def __init__(self, sink: Callable[[str], None] | None):
        self.sink = sink
        self.buf = ""
        self.inside = False

    def __call__(self, delta: str) -> None:
        if self.sink is None:
            return
        self.buf += delta
        out = []
        while self.buf:
            if self.inside:
                end = self.buf.find(self.CLOSE)
                if end < 0:
                    self.buf = self.buf[-(len(self.CLOSE) - 1):]  # keep a possible partial tag
                    break
                self.buf = self.buf[end + len(self.CLOSE):].lstrip("\n")
                self.inside = False
                continue
            start = self.buf.find(self.OPEN)
            if start >= 0:
                out.append(self.buf[:start])
                self.buf = self.buf[start + len(self.OPEN):]
                self.inside = True
                continue
            # emit all but a possible partial "<think" at the end
            keep = next((k for k in range(len(self.OPEN) - 1, 0, -1) if self.buf.endswith(self.OPEN[:k])), 0)
            out.append(self.buf[:len(self.buf) - keep])
            self.buf = self.buf[len(self.buf) - keep:]
            break
        text = "".join(out)
        if text:
            self.sink(text)


def clean_answer(text: str) -> str:
    text = _THINK_TAGS.sub("", text or "")
    return text.strip()
