"""Text-mode debug CLI.

Type exactly what you would say to the assistant; lines starting with ``:``
are debug commands (``:help``). It runs the same core pipeline as the tray
app, just without audio - milestone by milestone it gains intents, tools and
the LLM agent.
"""

from __future__ import annotations

import datetime as dt
import json
import shlex
import sys
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


class DebugCLI:
    def __init__(self, ctx: AppContext, *, out: TextIO | None = None,
                 read_line: Callable[[str], str] = input, tools: Any = None):
        self.ctx = ctx
        self.out = out or sys.stdout
        self.read_line = read_line
        self.commands: dict[str, tuple[Command, str]] = {}
        self.text_handler: Callable[[str], None] | None = None
        self._tools = tools
        self.raw_args = ""
        ctx.bus.subscribe("ui.notify", lambda e: self.print(
            f"\n[notification] {e.data.get('title', '')}: {e.data.get('message', '')}"))
        self._register_builtin()
        self._register_tools()

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
            return True
        if self.text_handler is None:
            self.print("(the text/intent pipeline arrives in milestone 3 - try :help)")
        else:
            self.text_handler(line)
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
