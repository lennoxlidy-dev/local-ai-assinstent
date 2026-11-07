"""Text-mode debug CLI.

Type exactly what you would say to the assistant; lines starting with ``:``
are debug commands (``:help``). It runs the same core pipeline as the tray
app, just without audio - milestone by milestone it gains intents, tools and
the LLM agent.
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import shlex
import sys
import threading
import time
from collections import deque
from collections.abc import Callable
from typing import Any, TextIO

from . import __version__
from .core import hardware
from .core.cancel import CancelToken
from .core.config import ConfigError, iter_settings
from .core.context import AppContext
from .permissions.gate import ConfirmChoice, ConfirmRequest, Task

Command = Callable[[list[str]], None]
log = logging.getLogger(__name__)

BANNER = """Assistant {version} - debug CLI ({name})
Type what you would say. Commands start with ':'  (:help, :quit)."""


class CliConfirmer:
    """Asks permission questions in the terminal."""

    def __init__(self, cli: DebugCLI):
        self.cli = cli

    def confirm(self, request: ConfirmRequest, cancel: CancelToken) -> ConfirmChoice:
        p = self.cli.print
        p("? Permission needed: " + request.text().replace("\n", "\n  "))
        for r in request.reasons:
            p(f"  ({r})")
        opts = {"y": ConfirmChoice.ONCE, "n": ConfirmChoice.DENY}
        labels = ["[y] yes, once"]
        if request.allow_task:
            opts["t"] = ConfirmChoice.TASK
            labels.append("[t] yes, for this task")
        if request.allow_always:
            opts["a"] = ConfirmChoice.ALWAYS
            labels.append(f"[a] always allow '{request.capability_title}'")
        labels.append("[n] no")
        p("  " + "   ".join(labels))
        while not cancel.cancelled:
            try:
                ans = self.cli.read_line("allow? ").strip().lower()[:1] or "n"
            except (EOFError, KeyboardInterrupt):
                return ConfirmChoice.DENY
            if ans in opts:
                return opts[ans]
        return ConfirmChoice.DENY


class CliVoiceConfirmer:
    """While the microphone runs in the CLI: answer permission questions by voice (yes / no)."""

    def __init__(self, cli: DebugCLI, pipeline: Any, fallback: Any):
        self.cli = cli
        self.pipeline = pipeline
        self.fallback = fallback

    def confirm(self, request: ConfirmRequest, cancel: CancelToken) -> ConfirmChoice:
        if not self.pipeline.can_confirm_by_voice():
            return self.fallback.confirm(request, cancel)
        self.cli.print("? Permission needed - say yes or no: " + request.text().replace("\n", "\n  "))
        choice = self.pipeline.confirm_by_voice(request, threading.Event())
        self.cli.print(f"  -> {choice or 'no clear answer (denied)'}")
        return choice or ConfirmChoice.DENY


class DebugCLI:
    def __init__(self, ctx: AppContext, *, out: TextIO | None = None,
                 read_line: Callable[[str], str] = input, tools: Any = None):
        self.ctx = ctx
        self.out = out or sys.stdout
        self.read_line = read_line
        self.commands: dict[str, tuple[Command, str]] = {}
        self.text_handler: Callable[[str], None] | None = None
        self._tools = tools
        self._assistant: Any = None
        self._streamed = False
        self.raw_args = ""
        ctx.bus.subscribe("ui.notify", lambda e: self.print(
            f"\n[notification] {e.data.get('title', '')}: {e.data.get('message', '')}"))
        self._voice: Any = None
        self._voice_unsub: Callable[[], None] | None = None
        self._register_builtin()
        self._register_tools()
        self._register_voice()

    @property
    def assistant(self) -> Any:
        """The full assistant (fast path + LLM agent), built on first use."""
        if self._assistant is None:
            from .core.assistant import Assistant
            from .llm.service import LlmService

            self._assistant = Assistant(self.ctx, self.tools, LlmService(self.ctx))
            self.ctx.bus.subscribe("agent.tool", self._on_tool_event)
            self.ctx.bus.subscribe("llm.state", lambda e: self.print(f"  (model {e.data['state']}: {e.data['detail']})")
                                   if e.data["state"] in ("starting", "failed") else None)
        return self._assistant

    def _on_tool_event(self, e: Any) -> None:
        d = e.data
        if d.get("phase") == "start":
            args = json.dumps(d.get("args", {}), ensure_ascii=False)
            self.print(f"\n  -> {d['name']} {args[:160]}")
        else:
            self.print(f"     {'ok' if d.get('ok') else 'FAILED'}: {d.get('text', '')[:160]}")

    def ask(self, text: str) -> None:
        """Free text: run it through the assistant, streaming the answer."""
        name = self.ctx.config.data.general.assistant_name.lower()
        self._streamed = False

        def stream(delta: str) -> None:
            if not self._streamed:
                self.out.write(f"{name}> ")
                self._streamed = True
            self.out.write(delta)
            self.out.flush()

        reply = self.assistant.handle(text, source="cli", on_content=stream)
        if self._streamed:
            self.print()
            if reply.via != "llm":
                self.print(f"{name}> {reply.text}")
        else:
            self.print(f"{name}> {reply.text}")
        t = reply.timings
        first = f", first token {t['input>llm_first_token']:.0f} ms" if "input>llm_first_token" in t else ""
        self.print(f"  [{reply.via}{(':' + reply.intent) if reply.intent else ''} in {t.get('total', 0):.0f} ms{first}]")

    @property
    def tools(self) -> Any:
        """The tool system, built on first use (keeps CLI startup fast)."""
        if self._tools is None:
            from .tools.runtime import build_tool_system

            self._tools = build_tool_system(self.ctx, confirmer=CliConfirmer(self))
        return self._tools

    # ------------------------------------------------------------------ plumbing
    def print(self, *parts: object) -> None:
        print(*parts, file=self.out, flush=True)

    def command(self, name: str, help_text: str) -> Callable[[Command], Command]:
        def deco(fn: Command) -> Command:
            self.commands[name] = (fn, help_text)
            return fn
        return deco

    def run(self) -> int:
        self.print(BANNER.format(version=__version__, name=self.ctx.config.data.general.assistant_name))
        for w in self.ctx.config.warnings:
            self.print(f"! config: {w}")
        while True:
            try:
                line = self.read_line("you> ")
            except (EOFError, KeyboardInterrupt):
                self.print()
                return 0
            if not self.handle(line):
                return 0

    def handle(self, line: str) -> bool:
        """Process one input line. Returns False when the CLI should exit."""
        line = line.strip()
        if not line:
            return True
        if line.startswith(":"):
            # raw text after the command name (JSON arguments must not go through shlex)
            self.raw_args = line[1:].strip().partition(" ")[2].strip()
            try:
                parts = shlex.split(line[1:], posix=True)
            except ValueError as e:
                self.print(f"! {e}")
                return True
            if not parts:
                return True
            name, args = parts[0].lower(), parts[1:]
            if name in ("q", "quit", "exit"):
                return False
            entry = self.commands.get(name)
            if entry is None:
                self.print(f"! unknown command :{name} (try :help)")
                return True
            try:
                entry[0](args)
            except (ConfigError, KeyError, ValueError) as e:
                self.print(f"! {e.__class__.__name__}: {e}")
            except Exception as e:   # e.g. no microphone - report it, keep the CLI running
                log.exception("command :%s failed", name)
                self.print(f"! {e.__class__.__name__}: {e}")
            return True
        if self.text_handler is not None:
            self.text_handler(line)
        else:
            self.ask(line)
        return True

    # ------------------------------------------------------------------ built-in commands
    def _register_builtin(self) -> None:
        ctx = self.ctx

        @self.command("help", "list commands")
        def _help(args: list[str]) -> None:
            self.print("Commands:")
            for name, (_, text) in sorted(self.commands.items()):
                self.print(f"  :{name:<12} {text}")
            self.print("  :quit         exit")

        @self.command("status", "versions, paths, hardware, state")
        def _status(args: list[str]) -> None:
            hw = hardware.detect()
            gpu = hw.best_gpu
            self.print(f"version     {__version__} (Python {sys.version.split()[0]})")
            self.print(f"os          {hw.os}")
            self.print(f"cpu         {hw.cpu} ({hw.physical_cores} cores / {hw.logical_cores} threads)")
            self.print(f"ram         {hw.ram_total_mb} MB total, {hw.ram_available_mb} MB available")
            if gpu:
                self.print(f"gpu         {gpu.name}: {gpu.vram_total_mb} MB VRAM, {gpu.vram_free_mb} MB free "
                           f"(driver {gpu.driver or '?'}, via {gpu.source})")
            else:
                self.print("gpu         no NVIDIA GPU detected")
            self.print(f"config      {ctx.paths.config_file}")
            self.print(f"data        {ctx.paths.local_dir}")
            self.print(f"autostart   {ctx.autostart.state()}")
            self.print(f"state       {json.dumps(ctx.state.snapshot(), default=str)}")

        @self.command("config", "show settings (optionally filtered: :config voice)")
        def _config(args: list[str]) -> None:
            prefix = args[0] if args else ""
            for key, value, _tp, meta in iter_settings(ctx.config.data):
                if key.startswith(prefix):
                    doc = f"  # {meta['doc']}" if meta.get("doc") else ""
                    self.print(f"{key} = {json.dumps(value, ensure_ascii=False)}{doc}")

        @self.command("set", "change a setting: :set voice.silence_timeout_s 1.2")
        def _set(args: list[str]) -> None:
            if len(args) < 2:
                raise ValueError("usage: :set <key> <value>")
            value = ctx.config.set(args[0], " ".join(args[1:]), from_text=True)
            self.print(f"{args[0]} = {json.dumps(value, ensure_ascii=False)}")

        @self.command("reset", "reset a setting to its default")
        def _reset(args: list[str]) -> None:
            if len(args) != 1:
                raise ValueError("usage: :reset <key>")
            value = ctx.config.reset(args[0])
            self.print(f"{args[0]} = {json.dumps(value, ensure_ascii=False)}")

        @self.command("autostart", "show or change start-at-login: :autostart on|off")
        def _autostart(args: list[str]) -> None:
            if args and args[0].lower() in ("on", "off"):
                on = args[0].lower() == "on"
                if on:
                    ctx.autostart.enable()
                else:
                    ctx.autostart.disable()
                ctx.config.set("general.autostart", on)
            self.print(f"autostart: {ctx.autostart.state()}  (command: {ctx.autostart.command})")

        @self.command("logs", "show the last lines of the debug log: :logs 30")
        def _logs(args: list[str]) -> None:
            n = int(args[0]) if args else 20
            for name in ("assistant-debug.log", "cli-debug.log"):
                f = ctx.paths.logs_dir / name
                if f.exists():
                    self.print(f"--- {f}")
                    with open(f, encoding="utf-8", errors="replace") as fh:
                        for line in deque(fh, maxlen=n):
                            self.print(line.rstrip())

    # ------------------------------------------------------------------ tools (milestone 2)
    def _register_tools(self) -> None:
        @self.command("tools", "list tools (or show one: :tools read_file)")
        def _tools(args: list[str]) -> None:
            reg = self.tools.registry
            if args:
                t = reg.get(args[0])
                if t is None:
                    raise ValueError(f"no tool {args[0]}")
                self.print(f"{t.name}  [{t.capability}]\n  {t.description}")
                self.print("  " + json.dumps(t.params.to_json(), ensure_ascii=False, indent=2).replace("\n", "\n  "))
                return
            for name in reg.names():
                t = reg.get(name)
                self.print(f"  {name:<16} [{t.capability}] {t.description[:70]}")

        @self.command("tool", "run a tool: :tool volume {\"level\": 30}   (JSON args, may be omitted)")
        def _tool(args: list[str]) -> None:
            if not args:
                raise ValueError("usage: :tool <name> [json-args]")
            raw = self.raw_args.partition(" ")[2].strip() or "{}"
            try:
                tool_args = json.loads(raw)
            except json.JSONDecodeError as e:
                raise ValueError(f"arguments must be JSON: {e}") from None
            res = self.tools.invoke(args[0], tool_args, Task(source="cli"))
            mark = "ok" if res.ok else "failed"
            self.print(f"[{mark}] {res.text}")

        @self.command("log", "show the action log: :log 20  (or :log 20 delete)")
        def _log(args: list[str]) -> None:
            n = int(args[0]) if args else 15
            search = args[1] if len(args) > 1 else None
            for e in reversed(self.tools.action_log.recent(n, search=search)):
                when = dt.datetime.fromtimestamp(e.ts).strftime("%m-%d %H:%M:%S")
                outcome = f" -> {e.outcome}" if e.outcome else ""
                self.print(f"{when} {e.decision:<8} {e.tool:<14} {e.summary or ''}{outcome}"
                           + (f"  [{e.result[:80]}]" if e.result and e.outcome != "ok" else ""))

        @self.command("perm", "show permissions, or set one: :perm files.delete allow|ask|block")
        def _perm(args: list[str]) -> None:
            from .permissions.policy import CAPABILITIES, RISK_TITLES, Level

            store = self.tools.policy
            if len(args) == 2:
                store.set_level(args[0], Level(args[1].lower()))
                self.tools.reload_policy()
            pol = store.policy
            for c in CAPABILITIES:
                self.print(f"  {c.id:<18} {pol.level(c.id):<6} {c.title}")
            self.print("  always confirm: " + ", ".join(RISK_TITLES[r] for r in sorted(pol.always_confirm)))
            self.print(f"  folder rules: {len(pol.folders)} (see {self.ctx.paths.permissions_file})")

        @self.command("facts", "list remembered facts")
        def _facts(args: list[str]) -> None:
            facts = self.tools.services["memory"].all()
            for f in facts:
                self.print(f"  #{f.id} {f.text}")
            if not facts:
                self.print("  (nothing remembered yet)")

        @self.command("routines", "list routines")
        def _routines(args: list[str]) -> None:
            rs = self.tools.services["routines"].all()
            for r in rs:
                self.print(f"  {r.name}: " + " -> ".join(f"{s['tool']}{json.dumps(s.get('args', {}))}" for s in r.steps))
            if not rs:
                self.print("  (no routines yet)")

        @self.command("timers", "list pending timers/reminders")
        def _timers(args: list[str]) -> None:
            pending = self.tools.timers.pending()
            for t in pending:
                due = dt.datetime.fromtimestamp(t.due).strftime("%H:%M:%S")
                self.print(f"  #{t.id} {t.kind} '{t.label}' at {due} ({int(t.remaining_s)} s left)")
            if not pending:
                self.print("  (none)")

        # ---------------------------------------------------------------- assistant / LLM (milestone 3)
        @self.command("fast", "show what the fast path makes of a sentence (nothing is executed)")
        def _fast(args: list[str]) -> None:
            fi = self.assistant.fastpath.match(self.raw_args)
            if fi is None:
                self.print("  no fast-path match -> the LLM would handle it")
            else:
                self.print(f"  intent={fi.intent} tool={fi.tool} args={json.dumps(fi.args, ensure_ascii=False)}"
                           + (f" answer={fi.answer!r}" if fi.answer else ""))

        @self.command("new", "start a new conversation (forget the current context)")
        def _new(args: list[str]) -> None:
            self.assistant.new_conversation()
            self.print("  new conversation")

        @self.command("history", "list/search saved conversations: :history [search words]")
        def _history(args: list[str]) -> None:
            store = self.assistant.conversations
            if args:
                for conv, snip in store.search(self.raw_args, 20):
                    self.print(f"  #{conv.id} {conv.title}  …{snip}")
            else:
                for conv in store.recent(20):
                    when = dt.datetime.fromtimestamp(conv.updated).strftime("%m-%d %H:%M")
                    self.print(f"  #{conv.id} {when} {conv.title}")

        @self.command("llm", "language model: :llm status|start|stop|models|use <id>")
        def _llm(args: list[str]) -> None:
            from .llm.models import CATALOG, is_installed

            svc = self.assistant.llm
            sub = args[0] if args else "status"
            if sub == "status":
                self.print(f"  {svc.status()}")
                if svc.spec:
                    self.print(f"  model: {svc.spec.title} ({svc.reason})")
            elif sub == "start":
                svc.start(wait=True)
                self.print(f"  {svc.status()}")
            elif sub == "stop":
                svc.stop()
                self.print("  stopped (VRAM freed)")
            elif sub == "models":
                for m in CATALOG.values():
                    if m.hidden:
                        continue
                    have = "installed" if is_installed(m, self.ctx.paths.models_dir, vision=False) else \
                        f"{m.download_bytes / 2**30:.1f} GB to download"
                    self.print(f"  {m.id:<16} {m.title:<46} {have}")
            elif sub == "use" and len(args) == 2:
                svc.switch_model(args[1])
                self.print(f"  now using {svc.spec.title}")
            else:
                raise ValueError("usage: :llm status|start|stop|models|use <id>")

    # ------------------------------------------------------------------ voice (milestone 4)
    def _voice_stack(self) -> Any:
        """Voice models for calibration/enrollment (and :voice on), loaded on first use."""
        if self._voice is None:
            from .voice.runtime import build_voice

            self._voice = build_voice(self.ctx, self.assistant)
        return self._voice

    def _record(self, seconds: float, prompt: str) -> Any:
        from .voice.calibration import record, speech_level_ok

        self.read_line(f"{prompt}  [Enter, then speak] ")
        self.print(f"  recording {seconds:.0f} s ...")
        audio = record(seconds, self.ctx.config.data.voice.input_device)
        if not speech_level_ok(audio):
            self.print("  (that was very quiet - is the right microphone selected? :voice devices)")
        return audio

    def _register_voice(self) -> None:
        ctx = self.ctx
        shown = ("voice.", "assistant.reply", "tool.start", "ui.notify")

        def show(e: Any) -> None:
            if e.topic.startswith(shown) and e.topic not in ("voice.level", "ui.notify"):
                data = {k: v for k, v in e.data.items() if k not in ("gen",)}
                self.print(f"  [{time.strftime('%H:%M:%S')}] {e.topic} {json.dumps(data, ensure_ascii=False)[:200]}")

        @self.command("voice", "voice: :voice status|on|off|devices|say <text>")
        def _voice(args: list[str]) -> None:
            from .audio.sources import list_devices
            from .voice.models import VOICE_MODELS, installed, missing

            sub = args[0] if args else "status"
            v = ctx.config.data.voice
            if sub == "status":
                gone = missing(ctx.paths.models_dir)
                for mid, m in VOICE_MODELS.items():
                    state = "installed" if installed(mid, ctx.paths.models_dir) else "missing"
                    self.print(f"  {mid:<8} {m.title:<44} {state}")
                self.print(f"  wake word: {ctx.config.data.general.assistant_name!r} sensitivity {v.wake_sensitivity}"
                           f" variants {v.wake_variants}")
                self.print(f"  mic: {v.input_device or 'Windows default'}   speaker: "
                           f"{v.output_device or 'Windows default'}")
                running = self._voice is not None and self._voice.pipeline.running
                self.print(f"  listening: {'yes' if running else 'no'}" + ("" if not gone else
                           "   (run setup to download the missing models)"))
            elif sub == "devices":
                self.print("  input:  " + ("\n          ".join(list_devices("input")) or "(none)"))
                self.print("  output: " + ("\n          ".join(list_devices("output")) or "(none)"))
            elif sub == "on":
                stack = self._voice_stack()
                if stack.pipeline.running:
                    self.print("  already listening")
                    return
                stack.pipeline.start()      # raises if the microphone can't be opened
                self._voice_unsub = ctx.bus.subscribe("*", show)
                self.tools.gate.confirmer = CliVoiceConfirmer(self, stack.pipeline, CliConfirmer(self))
                self.print(f"  listening for {ctx.config.data.general.assistant_name!r} - :voice off to stop")
            elif sub == "off":
                if self._voice is not None and self._voice.pipeline.running:
                    self._voice.pipeline.stop()
                    if self._voice_unsub is not None:
                        self._voice_unsub()
                        self._voice_unsub = None
                    self.tools.gate.confirmer = CliConfirmer(self)
                self.print("  microphone closed")
            elif sub == "say" and len(args) > 1:
                from .core.cancel import CancelToken as Tok

                stack = self._voice_stack()
                lang = ctx.config.data.general.language
                stack.speaker.say(" ".join(args[1:]), lang, v.tts_voice_de if lang == "de" else v.tts_voice_en,
                                  v.tts_speed, Tok())
                stack.speaker.wait()
            else:
                raise ValueError("usage: :voice status|on|off|devices|say <text>")

        @self.command("setup", "the setup wizard as text (Enter = keep the suggestion, back, quit)")
        def _setup(args: list[str]) -> None:
            from .onboarding.env import WizardEnv
            from .onboarding.text import run_text_wizard
            from .onboarding.wizard import Wizard

            apps = self.tools.services["apps"]
            env = WizardEnv(ctx=ctx, policy=self.tools.policy, routines=self.tools.services["routines"],
                            app_names=lambda: [a.name for a in apps.apps()],
                            first_run=not ctx.config.data.general.onboarding_done)
            run_text_wizard(Wizard(env), self.read_line, self.print)

        @self.command("calibrate", "say the name 3 times to tune the wake word for your voice")
        def _calibrate(args: list[str]) -> None:
            from .voice.calibration import calibrate_wake_word

            stack = self._voice_stack()
            name = ctx.config.data.general.assistant_name
            clips = [self._record(2.5, f"  {i}/3: say just “{name}”") for i in (1, 2, 3)]
            res = calibrate_wake_word(ctx, stack.wake, stack.stt, clips)
            self.print(f"  detected {res.detected}/{res.total} at sensitivity {res.sensitivity}"
                       f"{'; also listening for ' + ', '.join(res.variants) if res.variants else ''}")
            if res.heard:
                self.print(f"  speech recognition heard: {res.heard}")
            if not res.ok:
                self.print("  Not reliable yet. Try again a bit louder/clearer, or pick a name with 2-3 syllables.")

        @self.command("enroll", "voice lock: record your voice (:enroll forget removes it)")
        def _enroll(args: list[str]) -> None:
            stack = self._voice_stack()
            if args and args[0] == "forget":
                stack.voicelock.forget()
                ctx.config.set("voice.voice_lock", False)
                self.print("  voiceprint deleted, voice lock off")
                return
            prompts = ["the quick brown fox jumps over the lazy dog", "what's the weather like tomorrow",
                       "open my documents folder and play some music"]
            clips = [self._record(4, f"  {i}/3: read aloud: “{t}”") for i, t in enumerate(prompts, 1)]
            stack.voicelock.enroll(clips)
            ctx.config.set("voice.voice_lock", True)
            self.print("  enrolled - voice lock is on (:set voice.voice_lock false to turn it off)")
