"""Text-mode debug CLI.

Type exactly what you would say to the assistant; lines starting with ``:``
are debug commands (``:help``). It runs the same core pipeline as the tray
app, just without audio - milestone by milestone it gains intents, tools and
the LLM agent.
"""

from __future__ import annotations

import json
import shlex
import sys
from collections import deque
from typing import TextIO
from collections.abc import Callable

from . import __version__
from .core import hardware
from .core.config import ConfigError, iter_settings
from .core.context import AppContext

Command = Callable[[list[str]], None]

BANNER = """Assistant {version} - debug CLI ({name})
Type what you would say. Commands start with ':'  (:help, :quit)."""


class DebugCLI:
    def __init__(self, ctx: AppContext, *, out: TextIO | None = None,
                 read_line: Callable[[str], str] = input):
        self.ctx = ctx
        self.out = out or sys.stdout
        self.read_line = read_line
        self.commands: dict[str, tuple[Command, str]] = {}
        self.text_handler: Callable[[str], None] | None = None
        self._register_builtin()

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
