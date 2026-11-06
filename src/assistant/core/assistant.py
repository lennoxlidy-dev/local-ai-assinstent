"""The assistant's brain: text in -> fast path or LLM agent -> spoken/displayed reply.

Used by the debug CLI now, the voice pipeline (M4) and the popup's mini chat
(M4/M6) later. One request runs at a time; ``cancel()`` (panic hotkey,
"<name>, stop") aborts it from any thread and releases mouse/keyboard.
"""

from __future__ import annotations

import datetime as dt
import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any

from ..intents.fastpath import FastIntent, FastPath, failure_text
from ..llm.agent import Agent
from ..llm.client import LLMError
from ..llm.prompts import Profile, context_line, system_prompt, wants_thinking
from ..permissions.gate import Task
from ..storage.conversations import ConversationStore
from .cancel import CancelToken, Cancelled
from .context import AppContext
from .logging_setup import redact
from .state import Phase
from .timing import Trace

if TYPE_CHECKING:
    from ..llm.service import LlmService
    from ..tools.runtime import ToolSystem

log = logging.getLogger(__name__)

HISTORY_CHAR_BUDGET = 16000

TEXTS = {
    "cancelled": ("Cancelled.", "Abgebrochen."),
    "ok": ("OK.", "OK."),
    "no_llm": ("I can only do simple commands right now - the language model isn't available ({reason}).",
               "Ich kann gerade nur einfache Befehle - das Sprachmodell ist nicht verfügbar ({reason})."),
    "llm_error": ("Sorry, the language model had a problem: {reason}",
                  "Entschuldigung, das Sprachmodell hatte ein Problem: {reason}"),
    "busy": ("I'm still working on the last request.", "Ich bin noch mit der letzten Anfrage beschäftigt."),
}


@dataclass
class Reply:
    text: str
    ok: bool = True
    via: str = "fast"            # fast | local | llm | error | cancelled
    intent: str = ""
    calls: list[dict[str, Any]] = field(default_factory=list)
    timings: dict[str, float] = field(default_factory=dict)


class Assistant:
    def __init__(self, ctx: AppContext, tools: ToolSystem, llm: LlmService | None = None):
        self.ctx = ctx
        self.tools = tools
        self.llm = llm
        self.conversations = ConversationStore(tools.db)
        self.fastpath = FastPath(self._resolve_app, lambda: [ctx.config.data.general.assistant_name],
                                 lambda: ctx.config.data.general.language)
        self.agent = Agent(llm, tools, bus=ctx.bus, max_steps=ctx.config.data.llm.max_agent_steps) if llm else None
        self._lock = threading.Lock()
        self._current: Task | None = None
        self.root_cancel = CancelToken()
        self._history: list[dict[str, Any]] = []
        self._conv_id: int | None = None
        self._last = 0.0
        if llm is not None:
            from ..llm import vision

            vision.register(tools.registry, llm)
            llm.warmup_messages = self._warmup_messages
        purged = self.conversations.purge(ctx.config.data.privacy.retention_days)
        if purged:
            log.info("retention: deleted %d old conversations", purged)

    # ------------------------------------------------------------------ helpers
    def _t(self, key: str, **kw: Any) -> str:
        en, de = TEXTS[key]
        return (de if self.lang == "de" else en).format(**kw)

    @property
    def lang(self) -> str:
        return self.ctx.config.data.general.language

    def _resolve_app(self, name: str) -> str | None:
        try:
            app, _ = self.tools.services["apps"].matcher().match(name)
        except Exception as e:  # app index unavailable -> let the LLM handle it
            log.debug("app match failed: %s", e)
            return None
        return app.name if app is not None else None

    def profile(self) -> Profile:
        g = self.ctx.config.data.general
        facts = [f.text for f in self.tools.services["memory"].all()]
        return Profile(g.assistant_name, g.user_name, g.language, g.personality, g.answer_length, facts)

    def context(self) -> str:
        p = self.tools.platform
        win = None
        try:
            w = p.windows.foreground()
            if w is not None:
                win = f"{w.title} ({w.exe})" if w.exe else w.title
        except Exception:
            pass
        desk = None
        try:
            desks = p.desktops.list()
            cur = next((d for d in desks if d.current), None)
            if cur is not None:
                desk = f"{cur.number} of {len(desks)}" + (f" ({cur.name})" if cur.name else "")
        except Exception:
            pass
        return context_line(dt.datetime.now(), win, desk, self.lang)

    def _warmup_messages(self) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
        return ([{"role": "system", "content": system_prompt(self.profile())},
                 {"role": "user", "content": "hi"}], self.tools.registry.schemas())

    # ------------------------------------------------------------------ conversation
    def new_conversation(self) -> None:
        self._history, self._conv_id = [], None

    def _ensure_conversation(self) -> None:
        timeout = self.ctx.config.data.llm.conversation_timeout_min * 60
        if self._last and time.time() - self._last > timeout:
            self.new_conversation()
        self._last = time.time()

    def _persist(self, role: str, content: str, meta: dict[str, Any] | None = None) -> None:
        if not self.ctx.config.data.privacy.save_conversations or not content:
            return
        try:
            if self._conv_id is None:
                self._conv_id = self.conversations.create(language=self.lang).id
            self.conversations.add(self._conv_id, role, content, meta)
        except Exception:
            log.exception("could not save conversation")

    def _trim_history(self) -> None:
        total = sum(len(str(m.get("content") or "")) for m in self._history)
        while total > HISTORY_CHAR_BUDGET and self._history:
            # drop the oldest whole turn (user message + everything until the next user message)
            cut = next((i for i, m in enumerate(self._history[1:], 1) if m["role"] == "user"), len(self._history))
            total -= sum(len(str(m.get("content") or "")) for m in self._history[:cut])
            del self._history[:cut]

    # ------------------------------------------------------------------ control
    @property
    def busy(self) -> bool:
        return self._current is not None

    def cancel(self, reason: str = "stop") -> bool:
        """Panic / stop: cancel the running request and release mouse + keyboard."""
        task = self._current
        try:
            self.tools.platform.input.release_all()
        except Exception:
            pass
        if task is None:
            return False
        log.info("cancelling task %s (%s)", task.id, reason)
        task.cancel.cancel(reason)
        return True

    # ------------------------------------------------------------------ main entry
    def handle(self, text: str, *, source: str = "text",
               on_content: Callable[[str], None] | None = None) -> Reply:
        text = text.strip()
        if not text:
            return Reply("", via="local")
        trace = Trace()
        trace.mark("input")
        fi = self.fastpath.match(text)
        trace.mark("fastpath")
        if fi is not None and fi.action == "cancel":
            return self._stop(trace)
        if not self._lock.acquire(blocking=False):
            return Reply(self._t("busy"), ok=False, via="error")
        task = Task(source="fast" if fi is not None else "llm", cancel=self.root_cancel.child())
        task.cancel.on_cancel(lambda: self._safe_release())
        self._current = task
        try:
            self._ensure_conversation()
            log.info("request (%s): %s", source, redact(text, self.ctx.config.data.privacy.log_transcripts))
            self.ctx.state.update(phase=Phase.THINKING)
            self.ctx.bus.publish("assistant.request", text=text, source=source, task_id=task.id)
            if fi is not None:
                reply = self._run_fast(fi, task)
            else:
                reply = self._run_llm(text, task, trace, on_content)
            if reply.via in ("fast", "local"):  # LLM turns already added their own messages
                self._history += [{"role": "user", "content": text}, {"role": "assistant", "content": reply.text}]
            self._trim_history()
            self._persist("user", text, {"source": source})
            self._persist("assistant", reply.text, {"via": reply.via, "intent": reply.intent,
                                                    "calls": [c["name"] for c in reply.calls]})
        except Cancelled:
            reply = Reply(self._t("cancelled"), ok=False, via="cancelled")
        finally:
            self._current = None
            self._lock.release()
            self.ctx.state.update(phase=Phase.DONE)
        trace.mark("answer")
        reply.timings = trace.finish((("input", "fastpath"), ("input", "llm_first_token"), ("input", "answer")))
        self.ctx.bus.publish("assistant.reply", text=reply.text, ok=reply.ok, via=reply.via, intent=reply.intent)
        self.ctx.state.update(phase=Phase.IDLE)
        return reply

    def _safe_release(self) -> None:
        try:
            self.tools.platform.input.release_all()
        except Exception:
            pass

    def _stop(self, trace: Trace) -> Reply:
        if self.cancel("stop"):
            return Reply(self._t("cancelled"), via="local", intent="stop")
        try:  # nothing running: "stop" means "stop the music"
            st = self.tools.platform.media.status()
            if st and st.get("status") == "playing":
                self.tools.invoke("media", {"action": "pause"}, Task(source="fast"))
        except Exception:
            pass
        return Reply(self._t("ok"), via="local", intent="stop")

    # ------------------------------------------------------------------ fast path
    def _run_fast(self, fi: FastIntent, task: Task) -> Reply:
        if fi.answer is not None:
            return Reply(fi.answer, via="local", intent=fi.intent)
        assert fi.tool is not None
        self.ctx.state.update(phase=Phase.ACTING)
        res = self.tools.invoke(fi.tool, fi.args, task)
        calls = [{"name": fi.tool, "args": fi.args, "ok": res.ok}]
        if not res.ok and fi.fallback:
            for name, args in fi.fallback:
                if name == "wait":
                    if task.cancel.wait(float(args.get("seconds", 1))):
                        raise Cancelled(task.cancel.reason)
                    continue
                res = self.tools.invoke(name, args, task)
                calls.append({"name": name, "args": args, "ok": res.ok})
                if not res.ok:
                    break
        text = (fi.reply(res, self.lang) if fi.reply else res.text) if res.ok else failure_text(res, self.lang)
        return Reply(text, ok=res.ok, via="fast", intent=fi.intent, calls=calls)

    # ------------------------------------------------------------------ LLM agent
    def _run_llm(self, text: str, task: Task, trace: Trace, on_content: Callable[[str], None] | None) -> Reply:
        if self.agent is None or self.llm is None:
            return Reply(self._t("no_llm", reason="not configured"), ok=False, via="error")
        thinking = wants_thinking(text, self.ctx.config.data.llm.thinking)
        try:
            if not self.llm.ready:
                self.ctx.bus.publish("assistant.status", text="loading model")
            result = self.agent.run(text, system=system_prompt(self.profile()), context=self.context(),
                                    history=list(self._history), task=task, thinking=thinking, trace=trace,
                                    on_content=on_content, language=self.lang)
        except Cancelled:
            raise
        except LLMError as e:
            name = type(e).__name__
            key = "no_llm" if name == "LlmUnavailable" else "llm_error"
            return Reply(self._t(key, reason=str(e)[:160]), ok=False, via="error")
        self._history += result.messages
        return Reply(result.answer, ok=True, via="llm", calls=result.calls)
